import base64
from io import BytesIO

import pyotp
import qrcode
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from ..mfa import (
    begin_pending_mfa_login,
    clear_local_mfa_verified,
    complete_pending_mfa_login,
    get_or_create_mfa_device,
    get_pending_mfa_user,
    get_totp_issuer,
    local_mfa_is_verified,
    mark_local_mfa_verified,
    pending_mfa_next_url,
    user_requires_local_mfa,
    verify_totp_code,
)


def _safe_next_url(request):
    next_url = (
        request.POST.get("next")
        or request.GET.get("next")
        or pending_mfa_next_url(request)
        or ""
    ).strip()
    fallback = reverse("home")

    if not next_url:
        return fallback

    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return fallback

    blocked = {
        reverse("mfa_setup"),
        reverse("mfa_verify"),
        reverse("reset_mfa"),
        reverse("login"),
        reverse("logout"),
    }
    if next_url in blocked:
        return fallback

    return next_url


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
    if user and user.is_authenticated and user_requires_local_mfa(user):
        return user

    return None


def _finish_mfa(request, user):
    """Finish MFA and ensure the user is fully logged in only after success."""
    if get_pending_mfa_user(request):
        return complete_pending_mfa_login(request, user)

    mark_local_mfa_verified(request, user)
    return _safe_next_url(request)


def mfa_setup(request):
    user = _mfa_subject_user(request)
    if not user:
        messages.warning(request, _("Please sign in before setting up MFA."))
        return redirect("login")

    if not user_requires_local_mfa(user):
        return redirect("login")

    device = get_or_create_mfa_device(user)

    if device.confirmed:
        return redirect("mfa_verify")

    clear_local_mfa_verified(request)

    totp = pyotp.TOTP(device.secret)
    label = user.email or user.get_username()
    otpauth_uri = totp.provisioning_uri(name=label, issuer_name=get_totp_issuer())

    if request.method == "POST":
        if verify_totp_code(device, request.POST.get("code")):
            device.mark_confirmed()
            next_url = _finish_mfa(request, user)
            messages.success(request, _("Authenticator setup completed successfully."))
            return redirect(next_url)

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

    if not user_requires_local_mfa(user):
        return redirect("login")

    device = getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed:
        return redirect("mfa_setup")

    if request.user.is_authenticated and request.user.pk == user.pk and local_mfa_is_verified(request):
        return redirect(_safe_next_url(request))

    if request.method == "POST":
        if verify_totp_code(device, request.POST.get("code")):
            device.mark_verified()
            next_url = _finish_mfa(request, user)
            messages.success(request, _("MFA verification successful."))
            return redirect(next_url)

        messages.error(request, _("Invalid authenticator code. Please try again."))

    return render(request, "mfa_verify.html", {"next": _safe_next_url(request), "mfa_user": user})


@login_required
def reset_mfa(request):
    if request.method != "POST":
        return redirect("profile")

    user = request.user
    if not user_requires_local_mfa(user):
        messages.info(request, _("MFA reset is currently available for local DjOpenKB accounts only."))
        return redirect("profile")

    device = get_or_create_mfa_device(user)
    device.secret = pyotp.random_base32()
    device.confirmed = False
    device.confirmed_at = None
    device.last_verified_at = None
    device.reset_at = timezone.now()
    device.save(update_fields=["secret", "confirmed", "confirmed_at", "last_verified_at", "reset_at"])

    # MFA is a login criterion. After reset, the old authenticated session is no
    # longer allowed. Convert it to a pending-MFA session and force setup now.
    next_url = reverse("profile")
    backend = getattr(user, "backend", None)
    logout(request)
    begin_pending_mfa_login(request, user, next_url=next_url, backend=backend)

    messages.warning(request, _("Your MFA was reset. Complete authenticator setup now to continue using DjOpenKB."))
    return redirect("mfa_setup")
