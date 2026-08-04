import base64
from io import BytesIO

import pyotp
import qrcode
from django.contrib import messages
from django.contrib.auth import authenticate, logout
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from .services import is_ldap_managed_user, main_site_login_required

from ..auth_monitoring import (
    build_auth_lockout_ui_context,
    format_retry_after,
    get_auth_lockout_status,
    log_auth_event,
    record_auth_failure,
    record_auth_success,
)
from ..mfa import (
    begin_pending_mfa_login,
    begin_pending_mfa_reset,
    clear_user_auth_sessions,
    clear_mfa_verified,
    clear_pending_mfa_login,
    clear_pending_mfa_reset,
    complete_pending_mfa_login,
    defer_pending_mfa_deadline_for_lockout,
    get_or_create_mfa_device,
    get_mfa_login_timeout_seconds,
    get_pending_mfa_reset,
    get_pending_mfa_user,
    get_totp_issuer,
    mfa_device_secret_is_readable,
    mfa_is_verified,
    mark_mfa_verified,
    pending_mfa_challenge_matches,
    pending_mfa_reset_challenge_matches,
    pending_mfa_reset_device_matches,
    ensure_pending_mfa_challenge_id,
    pending_mfa_login_has_expired,
    pending_mfa_next_url,
    pending_mfa_seconds_remaining,
    pending_mfa_target_name,
    resume_pending_mfa_deadline_after_lockout,
    start_disabled_account_session,
    user_requires_mfa,
    verify_totp_code,
    verify_totp_secret,
)
from ..permissions import user_has_disabled_role


PROFILE_DIALOG_RESET_MFA = "reset_mfa"


def _reset_mfa_dialog_redirect():
    return redirect(f"{reverse('profile')}?dialog={PROFILE_DIALOG_RESET_MFA}")


def _mfa_reset_verification_failed_message(request):
    messages.error(
        request,
        _("Unable to verify the information provided. Please try again."),
    )


def _mfa_reset_verification_lockout_message(request, retry_after):
    messages.error(
        request,
        _("Too many unsuccessful verification attempts. Please try again in %(duration)s.")
        % {"duration": format_retry_after(retry_after)},
    )


def _deny_disabled_account_after_mfa(request, user, *, source):
    """Stop Disabled User accounts after successful MFA validation."""
    log_auth_event(
        request,
        event_type="mfa_verify_failure",
        success=False,
        user=user,
        username=user.get_username(),
        details={"reason": "account_disabled", "source": source},
    )
    start_disabled_account_session(request, user)
    return redirect("account_disabled")


def _blocked_next_paths():
    blocked = {
        reverse("mfa_setup"),
        reverse("mfa_verify"),
        reverse("reset_mfa"),
        reverse("mfa_reset_setup"),
        reverse("mfa_reset_cancel"),
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
    """Return pending MFA user first, otherwise the authenticated user."""
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


def _ensure_pending_mfa_login_for_timeout(request):
    """Ensure an MFA page always has a password-to-MFA deadline.

    The normal login flow already creates a pending-MFA session. This fallback
    handles older/stale authenticated sessions that reached the MFA page without
    the pending session keys, which would otherwise hide the countdown and leave
    no one-minute server-side deadline to enforce.
    """
    pending_user = get_pending_mfa_user(request)
    if pending_user:
        return pending_user

    user = getattr(request, "user", None)
    if (
        not user
        or not user.is_authenticated
        or not user_requires_mfa(user)
        or mfa_is_verified(request)
    ):
        return None

    backend = request.session.get("_auth_user_backend") or getattr(user, "backend", None)
    next_url = _safe_next_url(request)

    # Replace the premature authenticated session with the normal pending-MFA
    # state. Django will create the real login session only after TOTP succeeds.
    logout(request)
    begin_pending_mfa_login(
        request,
        user,
        next_url=next_url,
        backend=backend,
    )
    return user


def _prepare_pending_mfa_timing(request, user):
    """Make the longer MFA cooldown authoritative over the short login timer."""
    locked, retry_after, identifier = get_auth_lockout_status(
        request,
        user=user,
        purpose="mfa",
    )
    if locked:
        defer_pending_mfa_deadline_for_lockout(request)
    else:
        resume_pending_mfa_deadline_after_lockout(request)
    return locked, retry_after, identifier


def _mfa_rate_limit_context(request, user, *, state=None):
    """Return the current MFA cooldown UI without changing the lockout."""
    if state is None:
        state = get_auth_lockout_status(
            request,
            user=user,
            purpose="mfa",
        )
    locked, retry_after, _identifier = state
    return build_auth_lockout_ui_context(
        locked=locked,
        retry_after_seconds=retry_after,
        message=_(
            "Too many incorrect MFA codes. Please try again in %(duration)s."
        ),
        prefix="mfa_rate_limit",
    )


def _mfa_timeout_context(request):
    """Return countdown values derived from an already-started deadline."""
    remaining = pending_mfa_seconds_remaining(request, start_if_missing=False)
    if remaining is None:
        remaining_display = ""
    else:
        remaining_display = f"{max(0, int(remaining))}s"

    return {
        "mfa_login_timeout_active": remaining is not None,
        "mfa_login_timeout_seconds": get_mfa_login_timeout_seconds(),
        "mfa_login_timeout_remaining_seconds": remaining,
        "mfa_login_timeout_remaining_display": remaining_display,
        "mfa_login_challenge_id": ensure_pending_mfa_challenge_id(request) or "",
    }


def _mfa_page_security_context(request, user):
    state = _prepare_pending_mfa_timing(request, user)
    return {
        **_mfa_timeout_context(request),
        **_mfa_rate_limit_context(request, user, state=state),
    }


def _expire_pending_mfa_login(request, *, user=None, source="server_deadline"):
    """Clear a password-authenticated pending login after its MFA deadline."""
    user = user or get_pending_mfa_user(request)
    if user:
        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={
                "reason": "pending_mfa_timeout",
                "source": source,
                "timeout_seconds": get_mfa_login_timeout_seconds(),
            },
        )

    clear_mfa_verified(request)
    clear_pending_mfa_login(request)
    request.session.flush()
    messages.warning(
        request,
        _("MFA verification timed out. Please enter your username and password again."),
    )

    response = redirect("root_login")
    try:
        from kb.middleware import set_strict_no_cache_headers
    except Exception:
        set_strict_no_cache_headers = None
    if set_strict_no_cache_headers:
        response = set_strict_no_cache_headers(response)
    response["Clear-Site-Data"] = '"cache"'
    return response


def _enforce_pending_mfa_timeout(request):
    if pending_mfa_login_has_expired(request):
        return _expire_pending_mfa_login(request)
    return None


@require_POST
def cancel_mfa_login(request):
    """Cancel a password-authenticated pending-MFA login and return to login.

    At this stage the user is not fully signed in yet, so this is intentionally
    separate from the normal logout URL. It only clears the temporary MFA-login
    session state and sends the browser back to the login entry page.
    """
    pending_user = get_pending_mfa_user(request)
    timed_out = (request.POST.get("reason") or "").strip().lower() == "timeout"

    if pending_user and not pending_mfa_challenge_matches(
        request,
        request.POST.get("challenge_id"),
    ):
        # An old browser tab must not cancel or expire a newer password/MFA
        # attempt in the same browser session. Return to the current challenge.
        return redirect(pending_mfa_target_name(request) or "mfa_verify")

    if pending_user and timed_out:
        # The browser countdown is only a display aid. Re-check the current
        # server-side MFA cooldown before honouring expiry because a lockout may
        # have started in another tab or request after this page was rendered.
        # The longer cooldown always takes precedence over the short login timer.
        locked, _retry_after, _identifier = get_auth_lockout_status(
            request,
            user=pending_user,
            purpose="mfa",
        )
        if locked:
            defer_pending_mfa_deadline_for_lockout(request)
            return redirect(pending_mfa_target_name(request) or "mfa_verify")

        # A crafted early timeout request must not cancel a still-valid or
        # lockout-deferred challenge.
        if pending_mfa_login_has_expired(request):
            return _expire_pending_mfa_login(
                request,
                user=pending_user,
                source="countdown",
            )
        return redirect(pending_mfa_target_name(request) or "mfa_verify")

    if pending_user:
        try:
            from kb.middleware import clear_session_started_at, set_strict_no_cache_headers
        except Exception:
            clear_session_started_at = None
            set_strict_no_cache_headers = None

        clear_mfa_verified(request)
        clear_pending_mfa_login(request)
        if clear_session_started_at:
            clear_session_started_at(request)

        # The password step succeeded earlier, so rotate to a fresh anonymous
        # session when the user cancels before completing MFA.
        request.session.flush()
        messages.info(request, _("MFA sign-in was cancelled. Please sign in again."))
        response = redirect("root_login")
        if set_strict_no_cache_headers:
            set_strict_no_cache_headers(response)
        response["Clear-Site-Data"] = '"cache"'
        return response

    if getattr(request, "user", None) and request.user.is_authenticated:
        return redirect("home")

    return redirect("root_login")


def mfa_setup(request):
    _ensure_pending_mfa_login_for_timeout(request)

    user = _mfa_subject_user(request)
    if not user:
        messages.warning(request, _("Please sign in before setting up MFA."))
        return redirect("login")

    if not user_requires_mfa(user):
        return redirect("login")

    _prepare_pending_mfa_timing(request, user)
    timeout_response = _enforce_pending_mfa_timeout(request)
    if timeout_response is not None:
        return timeout_response

    device = get_or_create_mfa_device(user)

    if device.confirmed:
        return redirect("mfa_verify")

    clear_mfa_verified(request)

    if request.method == "POST" and not pending_mfa_challenge_matches(
        request,
        request.POST.get("challenge_id"),
    ):
        return redirect("mfa_setup")

    secret = device.get_secret()
    totp = pyotp.TOTP(secret)
    label = user.email or user.get_username()
    otpauth_uri = totp.provisioning_uri(name=label, issuer_name=get_totp_issuer())

    if request.method == "POST":
        locked, retry_after, identifier = get_auth_lockout_status(
            request,
            user=user,
            purpose="mfa",
        )
        if locked:
            defer_pending_mfa_deadline_for_lockout(request)
            log_auth_event(
                request,
                event_type="mfa_setup_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details={
                    "reason": "temporary_lockout",
                    "lockout_identifier": identifier,
                    "retry_after_seconds": retry_after,
                },
            )
            messages.error(
                request,
                _("Too many incorrect MFA codes. Please try again in %(duration)s.")
                % {"duration": format_retry_after(retry_after)},
            )
        elif verify_totp_code(device, request.POST.get("code")):
            record_auth_success(request, user=user, purpose="mfa")
            device.mark_confirmed()
            log_auth_event(
                request,
                event_type="mfa_setup_success",
                success=True,
                user=user,
                username=user.get_username(),
            )
            if user_has_disabled_role(user):
                return _deny_disabled_account_after_mfa(request, user, source="mfa_setup")
            next_url = _finish_mfa(request, user)
            messages.success(request, _("Authenticator setup completed successfully."))
            return redirect(next_url)
        else:
            lockout = record_auth_failure(request, user=user, purpose="mfa")
            details = {
                "reason": "invalid_totp",
                "lockout_identifier": lockout.get("identifier"),
                "failure_count": lockout.get("failure_count"),
                "failure_limit": lockout.get("failure_limit"),
            }
            if lockout.get("locked"):
                defer_pending_mfa_deadline_for_lockout(request)
                details["reason"] = "temporary_lockout_created"
                details["retry_after_seconds"] = lockout.get("retry_after_seconds")
                messages.error(
                    request,
                    _("Too many incorrect MFA codes. Please try again in %(duration)s.")
                    % {"duration": format_retry_after(lockout.get("retry_after_seconds"))},
                )

            log_auth_event(
                request,
                event_type="mfa_setup_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details=details,
            )
            messages.error(request, _("Invalid authenticator code. Please try again."))

    return render(
        request,
        "mfa_setup.html",
        {
            "qr_code_data_uri": _qr_data_uri(otpauth_uri),
            "manual_secret": secret,
            "next": _safe_next_url(request),
            "mfa_user": user,
            **_mfa_page_security_context(request, user),
        },
    )


def mfa_verify(request):
    _ensure_pending_mfa_login_for_timeout(request)

    user = _mfa_subject_user(request)
    if not user:
        messages.warning(request, _("Please sign in before verifying MFA."))
        return redirect("login")

    if not user_requires_mfa(user):
        return redirect("login")

    _prepare_pending_mfa_timing(request, user)
    timeout_response = _enforce_pending_mfa_timeout(request)
    if timeout_response is not None:
        return timeout_response

    device = getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed:
        return redirect("mfa_setup")

    if request.user.is_authenticated and request.user.pk == user.pk and mfa_is_verified(request):
        return redirect(_safe_next_url(request))

    if request.method == "POST" and not pending_mfa_challenge_matches(
        request,
        request.POST.get("challenge_id"),
    ):
        return redirect("mfa_verify")

    if not mfa_device_secret_is_readable(device):
        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={"reason": "unreadable_mfa_secret"},
        )
        messages.error(
            request,
            _(
                "This MFA device cannot be verified because its secret could not be read. "
                "Ask an admin to reset your MFA, or reset it from the server command line if admins are locked out."
            ),
        )
        return render(
            request,
            "mfa_verify.html",
            {
                "next": _safe_next_url(request),
                "mfa_user": user,
                **_mfa_page_security_context(request, user),
            },
        )

    if request.method == "POST":
        locked, retry_after, identifier = get_auth_lockout_status(
            request,
            user=user,
            purpose="mfa",
        )
        if locked:
            defer_pending_mfa_deadline_for_lockout(request)
            log_auth_event(
                request,
                event_type="mfa_verify_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details={
                    "reason": "temporary_lockout",
                    "lockout_identifier": identifier,
                    "retry_after_seconds": retry_after,
                },
            )
            messages.error(
                request,
                _("Too many incorrect MFA codes. Please try again in %(duration)s.")
                % {"duration": format_retry_after(retry_after)},
            )
        elif verify_totp_code(device, request.POST.get("code")):
            record_auth_success(request, user=user, purpose="mfa")
            device.mark_verified()
            log_auth_event(
                request,
                event_type="mfa_verify_success",
                success=True,
                user=user,
                username=user.get_username(),
            )
            if user_has_disabled_role(user):
                return _deny_disabled_account_after_mfa(request, user, source="mfa_verify")
            next_url = _finish_mfa(request, user)
            full_name = (user.get_full_name() or "").strip()
            if full_name:
                welcome_message = _(
                    "Welcome back, %(name)s."
                ) % {
                    "name": full_name,
                }
            else:
                welcome_message = _("Welcome back.")

            messages.success(request, welcome_message)
            return redirect(next_url)
        else:
            lockout = record_auth_failure(request, user=user, purpose="mfa")
            details = {
                "reason": "invalid_totp",
                "lockout_identifier": lockout.get("identifier"),
                "failure_count": lockout.get("failure_count"),
                "failure_limit": lockout.get("failure_limit"),
            }
            if lockout.get("locked"):
                defer_pending_mfa_deadline_for_lockout(request)
                details["reason"] = "temporary_lockout_created"
                details["retry_after_seconds"] = lockout.get("retry_after_seconds")
                messages.error(
                    request,
                    _("Too many incorrect MFA codes. Please try again in %(duration)s.")
                    % {"duration": format_retry_after(lockout.get("retry_after_seconds"))},
                )

            log_auth_event(
                request,
                event_type="mfa_verify_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details=details,
            )
            messages.error(request, _("Invalid authenticator code. Please try again."))

    return render(
        request,
        "mfa_verify.html",
        {
            "next": _safe_next_url(request),
            "mfa_user": user,
            **_mfa_page_security_context(request, user),
        },
    )


def _verify_mfa_reset_password(request, user):
    """Require the account password again before a self-service MFA reset.

    Local accounts are checked against Django's password hash. AD-managed
    accounts are re-authenticated against the configured LDAP backend. The
    submitted password is never stored in the session or database.
    """
    current_password = request.POST.get("current_password", "")
    locked, retry_after, identifier = get_auth_lockout_status(
        request,
        user=user,
        purpose="password",
    )
    if locked:
        log_auth_event(
            request,
            event_type="password_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={
                "reason": "temporary_lockout",
                "source": "mfa_reset_self",
                "lockout_identifier": identifier,
                "retry_after_seconds": retry_after,
            },
        )
        _mfa_reset_verification_lockout_message(request, retry_after)
        return False

    if is_ldap_managed_user(user):
        # Do not trust a user-supplied login_mode here. The account's stored
        # authentication source decides whether LDAP re-authentication is used.
        verified_user = authenticate(
            request=None,
            username=user.get_username(),
            password=current_password,
        )
        password_valid = bool(verified_user and verified_user.pk == user.pk)
    else:
        password_valid = bool(user.has_usable_password() and user.check_password(current_password))

    if not password_valid:
        lockout = record_auth_failure(request, user=user, purpose="password")
        details = {
            "reason": "invalid_mfa_reset_password",
            "source": "mfa_reset_self",
            "lockout_identifier": lockout.get("identifier"),
            "failure_count": lockout.get("failure_count"),
            "failure_limit": lockout.get("failure_limit"),
        }
        if lockout.get("locked"):
            details["reason"] = "temporary_lockout_created"
            details["retry_after_seconds"] = lockout.get("retry_after_seconds")
        log_auth_event(
            request,
            event_type="password_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details=details,
        )
        if lockout.get("locked"):
            _mfa_reset_verification_lockout_message(
                request,
                lockout.get("retry_after_seconds"),
            )
        else:
            _mfa_reset_verification_failed_message(request)
        return False

    record_auth_success(request, user=user, purpose="password")
    log_auth_event(
        request,
        event_type="password_success",
        success=True,
        user=user,
        username=user.get_username(),
        details={"source": "mfa_reset_self"},
    )
    return True


def _verify_mfa_reset_code(request, user):
    """Require the currently configured TOTP before replacing its secret."""
    device = getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed:
        messages.error(request, _("Set up MFA before changing sensitive account details."))
        return False

    if not mfa_device_secret_is_readable(device):
        messages.error(
            request,
            _(
                "This MFA device cannot be verified because its secret could not be read. "
                "Ask an admin to reset your MFA, or reset it from the server command line if admins are locked out."
            ),
        )
        return False

    locked, retry_after, identifier = get_auth_lockout_status(
        request,
        user=user,
        purpose="mfa",
    )
    if locked:
        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={
                "reason": "temporary_lockout",
                "source": "mfa_reset_self",
                "lockout_identifier": identifier,
                "retry_after_seconds": retry_after,
            },
        )
        _mfa_reset_verification_lockout_message(request, retry_after)
        return False

    if not verify_totp_code(device, request.POST.get("mfa_code")):
        lockout = record_auth_failure(request, user=user, purpose="mfa")
        details = {
            "reason": "invalid_mfa_reset_totp",
            "source": "mfa_reset_self",
            "lockout_identifier": lockout.get("identifier"),
            "failure_count": lockout.get("failure_count"),
            "failure_limit": lockout.get("failure_limit"),
        }
        if lockout.get("locked"):
            details["reason"] = "temporary_lockout_created"
            details["retry_after_seconds"] = lockout.get("retry_after_seconds")
        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details=details,
        )
        if lockout.get("locked"):
            _mfa_reset_verification_lockout_message(
                request,
                lockout.get("retry_after_seconds"),
            )
        else:
            _mfa_reset_verification_failed_message(request)
        return False

    record_auth_success(request, user=user, purpose="mfa")
    device.mark_verified()
    log_auth_event(
        request,
        event_type="mfa_verify_success",
        success=True,
        user=user,
        username=user.get_username(),
        details={"reason": "mfa_reset_self_confirmed"},
    )
    return True


@main_site_login_required
@require_POST
def reset_mfa(request):
    user = request.user
    if not user_requires_mfa(user):
        messages.info(request, _("MFA reset is available for your Knowledge Repository account."))
        return redirect("profile")

    # A stolen authenticated browser session must not be enough to begin an MFA
    # replacement. Reverify both the account password and the currently active
    # authenticator code before generating a staged replacement secret.
    if not _verify_mfa_reset_password(request, user):
        return _reset_mfa_dialog_redirect()
    if not _verify_mfa_reset_code(request, user):
        return _reset_mfa_dialog_redirect()

    device = getattr(user, "kb_mfa_device", None)
    if not begin_pending_mfa_reset(request, user, device=device):
        messages.error(
            request,
            _("MFA reset could not be started. Please try again or contact an administrator."),
        )
        return _reset_mfa_dialog_redirect()

    log_auth_event(
        request,
        event_type="pending_mfa",
        success=True,
        user=user,
        username=user.get_username(),
        details={
            "source": "mfa_reset_self",
            "password_reverified": True,
            "current_mfa_reverified": True,
            "active_secret_replaced": False,
        },
    )

    return redirect("mfa_reset_setup")


@main_site_login_required
@require_http_methods(["GET", "POST"])
def mfa_reset_setup(request):
    """Confirm a staged replacement before changing the active MFA secret."""
    user = request.user
    state = get_pending_mfa_reset(request, user)
    if not state:
        messages.info(request, _("MFA setup expired. Please start again."))
        return redirect("profile")

    device = getattr(user, "kb_mfa_device", None)
    if (
        not device
        or not device.confirmed
        or not mfa_device_secret_is_readable(device)
        or not pending_mfa_reset_device_matches(request, user, device=device)
    ):
        clear_pending_mfa_reset(request)
        messages.warning(request, _("MFA setup could not be completed. Please start again."))
        return redirect("profile")

    if request.method == "POST" and not pending_mfa_reset_challenge_matches(
        request,
        user,
        request.POST.get("challenge_id"),
    ):
        return redirect("mfa_reset_setup")

    if request.method == "POST":
        locked, retry_after, identifier = get_auth_lockout_status(
            request,
            user=user,
            purpose="mfa",
        )
        if locked:
            log_auth_event(
                request,
                event_type="mfa_setup_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details={
                    "reason": "temporary_lockout",
                    "source": "mfa_reset_self_new_device",
                    "lockout_identifier": identifier,
                    "retry_after_seconds": retry_after,
                    "active_secret_replaced": False,
                },
            )
            _mfa_reset_verification_lockout_message(request, retry_after)
        elif verify_totp_secret(state["secret"], request.POST.get("code")):
            current_session_key = request.session.session_key
            with transaction.atomic():
                locked_device = type(device).objects.select_for_update().get(pk=device.pk)
                if not pending_mfa_reset_device_matches(
                    request,
                    user,
                    device=locked_device,
                ):
                    clear_pending_mfa_reset(request)
                    messages.warning(
                        request,
                        _("MFA setup could not be completed. Please start again."),
                    )
                    return redirect("profile")

                now = timezone.now()
                locked_device.set_secret(state["secret"])
                locked_device.confirmed = True
                locked_device.confirmed_at = now
                locked_device.last_verified_at = now
                locked_device.reset_at = now
                locked_device.save(
                    update_fields=[
                        "secret",
                        "confirmed",
                        "confirmed_at",
                        "last_verified_at",
                        "reset_at",
                    ]
                )
                other_sessions_deleted = clear_user_auth_sessions(
                    user,
                    exclude_session_key=current_session_key,
                )

            record_auth_success(request, user=user, purpose="mfa")
            clear_pending_mfa_reset(request)
            mark_mfa_verified(request, user)

            log_auth_event(
                request,
                event_type="mfa_setup_success",
                success=True,
                user=user,
                username=user.get_username(),
                details={"source": "mfa_reset_self_new_device"},
            )
            log_auth_event(
                request,
                event_type="mfa_reset_self",
                success=True,
                user=user,
                username=user.get_username(),
                details={
                    "password_reverified": True,
                    "current_mfa_reverified": True,
                    "new_mfa_verified": True,
                    "other_sessions_deleted": other_sessions_deleted,
                },
            )
            messages.success(request, _("MFA updated successfully."))
            return redirect("profile")
        else:
            lockout = record_auth_failure(request, user=user, purpose="mfa")
            details = {
                "reason": "invalid_new_totp",
                "source": "mfa_reset_self_new_device",
                "lockout_identifier": lockout.get("identifier"),
                "failure_count": lockout.get("failure_count"),
                "failure_limit": lockout.get("failure_limit"),
                "active_secret_replaced": False,
            }
            if lockout.get("locked"):
                details["reason"] = "temporary_lockout_created"
                details["retry_after_seconds"] = lockout.get("retry_after_seconds")
            log_auth_event(
                request,
                event_type="mfa_setup_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details=details,
            )
            if lockout.get("locked"):
                _mfa_reset_verification_lockout_message(
                    request,
                    lockout.get("retry_after_seconds"),
                )
            else:
                _mfa_reset_verification_failed_message(request)

    state = get_pending_mfa_reset(request, user)
    if not state:
        messages.info(request, _("MFA setup expired. Please start again."))
        return redirect("profile")

    totp = pyotp.TOTP(state["secret"])
    label = user.email or user.get_username()
    otpauth_uri = totp.provisioning_uri(name=label, issuer_name=get_totp_issuer())
    locked, retry_after, _identifier = get_auth_lockout_status(
        request,
        user=user,
        purpose="mfa",
    )

    return render(
        request,
        "mfa_reset_setup.html",
        {
            "qr_code_data_uri": _qr_data_uri(otpauth_uri),
            "manual_secret": state["secret"],
            "mfa_reset_challenge_id": state["challenge_id"],
            **build_auth_lockout_ui_context(
                locked=locked,
                retry_after_seconds=retry_after,
                message=_("Too many unsuccessful verification attempts. Please try again in %(duration)s."),
                prefix="mfa_rate_limit",
            ),
        },
    )


@main_site_login_required
@require_POST
def cancel_mfa_reset(request):
    clear_pending_mfa_reset(request)
    return redirect("profile")
