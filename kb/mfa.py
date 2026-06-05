import pyotp
from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.sessions.models import Session
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from .models import UserMFADevice


MFA_SESSION_KEY = "djopenkb_mfa_verified"
MFA_USER_SESSION_KEY = "djopenkb_mfa_verified_user_id"

PRE_MFA_USER_ID_SESSION_KEY = "djopenkb_pre_mfa_user_id"
PRE_MFA_BACKEND_SESSION_KEY = "djopenkb_pre_mfa_backend"
PRE_MFA_NEXT_SESSION_KEY = "djopenkb_pre_mfa_next"

# Backwards-compatible names from the earlier local-only MFA implementation.
LOCAL_MFA_SESSION_KEY = MFA_SESSION_KEY
LOCAL_MFA_USER_SESSION_KEY = MFA_USER_SESSION_KEY


def get_totp_issuer():
    return getattr(settings, "MFA_TOTP_ISSUER", "DjOpenKB")


def _configured_backend_contains(name_fragment):
    for backend in getattr(settings, "AUTHENTICATION_BACKENDS", []):
        if name_fragment in backend:
            return backend
    return None


def get_mfa_completion_backend(user=None):
    """Return the backend used when MFA completes the login.

    Password authentication already happened before MFA. This function only tells
    Django which backend should own the final authenticated session.
    """
    backend = getattr(user, "backend", None)
    if backend:
        return backend

    profile = getattr(user, "kb_profile", None)
    if profile and getattr(profile, "is_ldap_type", False):
        return (
            _configured_backend_contains("NextLabsLDAPBackend")
            or _configured_backend_contains("LDAPBackend")
            or _configured_backend_contains("PlaceholderLDAPBackend")
        )

    return (
        _configured_backend_contains("EmailOrUsernameModelBackend")
        or _configured_backend_contains("ModelBackend")
        or (
            list(getattr(settings, "AUTHENTICATION_BACKENDS", []))[-1]
            if getattr(settings, "AUTHENTICATION_BACKENDS", [])
            else None
        )
    )


# Backwards-compatible alias used by older imports.
get_local_mfa_backend = get_mfa_completion_backend


def user_requires_mfa(user):
    """Return True when the user must complete site-level TOTP MFA.

    MFA is a login criterion for both local Django accounts and LDAP/AD accounts.
    A password-authenticated user is not considered fully signed in until MFA
    setup/verification has completed.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if not getattr(user, "is_active", False):
        return False

    profile = getattr(user, "kb_profile", None)
    if profile and not getattr(profile, "can_access_main_site", True):
        return False

    return True


# Backwards-compatible name from the earlier local-only implementation.
def user_requires_local_mfa(user):
    return user_requires_mfa(user)


def get_or_create_mfa_device(user):
    device, _created = UserMFADevice.objects.get_or_create(
        user=user,
        defaults={"secret": pyotp.random_base32()},
    )
    if not device.secret:
        device.secret = pyotp.random_base32()
        device.save(update_fields=["secret"])
    return device


def mfa_status_label(user):
    device = getattr(user, "kb_mfa_device", None)
    if not user_requires_mfa(user):
        return "Not required"
    if not device:
        return "Not set up"
    if device.confirmed:
        return "Configured"
    return "Setup pending"


def reset_mfa_device_for_user(user):
    """Generate a fresh private TOTP secret and require setup again.

    The new secret is not shown to admins. The user must sign in again and scan
    their own QR code on the MFA setup page.
    """
    device = get_or_create_mfa_device(user)
    now = timezone.now()
    device.secret = pyotp.random_base32()
    device.confirmed = False
    device.confirmed_at = None
    device.last_verified_at = None
    device.reset_at = now
    device.save(
        update_fields=[
            "secret",
            "confirmed",
            "confirmed_at",
            "last_verified_at",
            "reset_at",
        ]
    )
    return device


def clear_user_auth_sessions(user):
    """Delete active sessions for a user after admin/user MFA reset.

    This prevents an already logged-in browser session from continuing after the
    MFA secret has been replaced.
    """
    deleted = 0
    user_id = str(user.pk)
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        data = session.get_decoded()
        if (
            str(data.get("_auth_user_id")) == user_id
            or str(data.get(PRE_MFA_USER_ID_SESSION_KEY)) == user_id
            or str(data.get(MFA_USER_SESSION_KEY)) == user_id
        ):
            session.delete()
            deleted += 1
    return deleted


def admin_reset_user_mfa(user):
    """Reset a user's MFA from Django admin and invalidate existing sessions."""
    device = reset_mfa_device_for_user(user)
    sessions_deleted = clear_user_auth_sessions(user)
    return device, sessions_deleted


def mfa_is_verified(request):
    """True only when the current authenticated session completed MFA."""
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return False

    return bool(
        request.session.get(MFA_SESSION_KEY)
        and constant_time_compare(str(request.session.get(MFA_USER_SESSION_KEY)), str(user.pk))
    )


# Backwards-compatible alias used by older imports.
local_mfa_is_verified = mfa_is_verified


def mark_mfa_verified(request, user=None):
    user = user or getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return

    request.session[MFA_SESSION_KEY] = True
    request.session[MFA_USER_SESSION_KEY] = str(user.pk)
    request.session.modified = True


# Backwards-compatible alias used by older imports.
mark_local_mfa_verified = mark_mfa_verified


def clear_mfa_verified(request):
    request.session.pop(MFA_SESSION_KEY, None)
    request.session.pop(MFA_USER_SESSION_KEY, None)
    request.session.modified = True


# Backwards-compatible alias used by older imports.
clear_local_mfa_verified = clear_mfa_verified


def clear_pending_mfa_login(request):
    request.session.pop(PRE_MFA_USER_ID_SESSION_KEY, None)
    request.session.pop(PRE_MFA_BACKEND_SESSION_KEY, None)
    request.session.pop(PRE_MFA_NEXT_SESSION_KEY, None)
    request.session.modified = True


def begin_pending_mfa_login(request, user, next_url=None, backend=None):
    """Store a password-authenticated but MFA-incomplete login.

    The real Django authenticated session is created only after TOTP
    setup/verification succeeds.
    """
    clear_mfa_verified(request)
    request.session[PRE_MFA_USER_ID_SESSION_KEY] = str(user.pk)
    request.session[PRE_MFA_BACKEND_SESSION_KEY] = backend or get_mfa_completion_backend(user)
    request.session[PRE_MFA_NEXT_SESSION_KEY] = next_url or reverse("home")
    request.session.modified = True


def get_pending_mfa_user(request):
    user_id = request.session.get(PRE_MFA_USER_ID_SESSION_KEY)
    if not user_id:
        return None

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        clear_pending_mfa_login(request)
        return None

    if not user_requires_mfa(user):
        clear_pending_mfa_login(request)
        return None

    return user


def pending_mfa_next_url(request, default=None):
    return request.session.get(PRE_MFA_NEXT_SESSION_KEY) or default or reverse("home")


def pending_mfa_target_name(request):
    """Return mfa_setup or mfa_verify for the current pending/authenticated user."""
    user = get_pending_mfa_user(request) or getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None

    device = getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed:
        return "mfa_setup"
    return "mfa_verify"


def complete_pending_mfa_login(request, user):
    """Promote the pending MFA session into a real authenticated session."""
    backend = request.session.get(PRE_MFA_BACKEND_SESSION_KEY) or get_mfa_completion_backend(user)
    next_url = pending_mfa_next_url(request)

    clear_pending_mfa_login(request)

    if backend:
        auth_login(request, user, backend=backend)
    else:
        auth_login(request, user)

    mark_mfa_verified(request, user)
    request.session.pop(PRE_MFA_NEXT_SESSION_KEY, None)
    request.session.modified = True
    return next_url


def verify_totp_code(device, code):
    code = (code or "").strip().replace(" ", "")
    if not code or not device or not device.secret:
        return False

    totp = pyotp.TOTP(device.secret)
    return bool(totp.verify(code, valid_window=1))
