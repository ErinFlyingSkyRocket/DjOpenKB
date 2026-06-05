import base64
from io import BytesIO

import pyotp
import qrcode
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .services import main_site_login_required

from ..auth_monitoring import log_auth_event
from ..mfa import (
    begin_pending_mfa_login,
    clear_mfa_verified,
    complete_pending_mfa_login,
    get_or_create_mfa_device,
    reset_mfa_device_for_user,
    get_pending_mfa_user,
    get_totp_issuer,
    mfa_is_verified,
    mark_mfa_verified,
    pending_mfa_next_url,
    user_requires_mfa,
    verify_totp_code,
)


def _blocked_next_paths():
    blocked = {
        reverse("mfa_setup"),
        reverse("mfa_verify"),
        reverse("reset_mfa"),
        reverse("login"),
        reverse("logout"),
    }
    try:
        blocked.add(reverse("admin:login"))
        blocked.add(reverse("admin:logout"))
    except Exception:
        blocked.update({"/admin/login/", "/admin/logout/"})
    return blocked


def _safe_next_url(request):
    fallback = reverse("home")

    # For a password-authenticated pending-MFA login, trust the server-side
    # destination saved when the password/LDAPS bind succeeded. Do not let a
    # middleware-added next=/admin/login/?next=/admin/ override it.
    if get_pending_mfa_user(request):
        candidates = [
            (pending_mfa_next_url(request) or "").strip(),
            (request.POST.get("next") or "").strip(),
            (request.GET.get("next") or "").strip(),
        ]
    else:
        candidates = [
            (request.POST.get("next") or "").strip(),
            (request.GET.get("next") or "").strip(),
            (pending_mfa_next_url(request) or "").strip(),
        ]

    blocked = _blocked_next_paths()
    for next_url in candidates:
        if not next_url:
            continue

        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            continue

        if next_url in blocked or any(next_url.startswith(f"{path}?") for path in blocked):
            continue

        return next_url

    return fallback


def _qr_data_uri(otpauth_uri):
    image = qrcode.make(otpauth_uri)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _mfa_subject_user(request):
    """Return pending MFA user first, otherwise authenticated legacy user."""
    pending_user = get_pending_mfa_user(request)
    if pending_user:
        return pending_user

    user = getattr(request, "user", None)
    if user and user.is_authenticated and user_requires_mfa(user):
        return user

    return None


def _finish_mfa(request, user):
    """Finish MFA and ensure the user is fully logged in only after success."""
    if get_pending_mfa_user(request):
        return complete_pending_mfa_login(request, user)

    mark_mfa_verified(request, user)
    return _safe_next_url(request)


def mfa_setup(request):
    user = _mfa_subject_user(request)
    if not user:
        messages.warning(request, _("Please sign in before setting up MFA."))
        return redirect("login")

    if not user_requires_mfa(user):
        return redirect("login")

    device = get_or_create_mfa_device(user)

    if device.confirmed:
        return redirect("mfa_verify")

    clear_mfa_verified(request)

    totp = pyotp.TOTP(device.secret)
    label = user.email or user.get_username()
    otpauth_uri = totp.provisioning_uri(name=label, issuer_name=get_totp_issuer())

    if request.method == "POST":
        if verify_totp_code(device, request.POST.get("code")):
            device.mark_confirmed()
            log_auth_event(
                request,
                event_type="mfa_setup_success",
                success=True,
                user=user,
                username=user.get_username(),
            )
            next_url = _finish_mfa(request, user)
            messages.success(request, _("Authenticator setup completed successfully."))
            return redirect(next_url)

        log_auth_event(
            request,
            event_type="mfa_setup_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={"reason": "invalid_totp"},
        )
        messages.error(request, _("Invalid authenticator code. Please try again."))

    return render(
        request,
        "mfa_setup.html",
        {
            "qr_code_data_uri": _qr_data_uri(otpauth_uri),
            "manual_secret": device.secret,
            "next": _safe_next_url(request),
            "mfa_user": user,
        },
    )


def mfa_verify(request):
    user = _mfa_subject_user(request)
    if not user:
        messages.warning(request, _("Please sign in before verifying MFA."))
        return redirect("login")

    if not user_requires_mfa(user):
        return redirect("login")

    device = getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed:
        return redirect("mfa_setup")

    if request.user.is_authenticated and request.user.pk == user.pk and mfa_is_verified(request):
        return redirect(_safe_next_url(request))

    if request.method == "POST":
        if verify_totp_code(device, request.POST.get("code")):
            device.mark_verified()
            log_auth_event(
                request,
                event_type="mfa_verify_success",
                success=True,
                user=user,
                username=user.get_username(),
            )
            next_url = _finish_mfa(request, user)
            messages.success(request, _("MFA verification successful."))
            return redirect(next_url)

        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={"reason": "invalid_totp"},
        )
        messages.error(request, _("Invalid authenticator code. Please try again."))

    return render(request, "mfa_verify.html", {"next": _safe_next_url(request), "mfa_user": user})


@main_site_login_required
@require_POST
def reset_mfa(request):
    user = request.user
    if not user_requires_mfa(user):
        messages.info(request, _("MFA reset is available for your DjOpenKB account."))
        return redirect("profile")

    reset_mfa_device_for_user(user)
    log_auth_event(
        request,
        event_type="mfa_reset_self",
        success=True,
        user=user,
        username=user.get_username(),
    )

    # MFA is a login criterion. After reset, the old authenticated session is no
    # longer allowed. Convert it to a pending-MFA session and force setup now.
    next_url = reverse("profile")
    backend = request.session.get("_auth_user_backend") or getattr(user, "backend", None)
    logout(request)
    begin_pending_mfa_login(request, user, next_url=next_url, backend=backend)

    messages.warning(request, _("Your MFA was reset. Complete authenticator setup now to continue using DjOpenKB."))
    return redirect("mfa_setup")
