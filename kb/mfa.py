import pyotp
from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.urls import reverse
from django.utils.crypto import constant_time_compare

from .models import UserMFADevice


LOCAL_MFA_SESSION_KEY = "djopenkb_local_mfa_verified"
LOCAL_MFA_USER_SESSION_KEY = "djopenkb_local_mfa_verified_user_id"

PRE_MFA_USER_ID_SESSION_KEY = "djopenkb_pre_mfa_user_id"
PRE_MFA_BACKEND_SESSION_KEY = "djopenkb_pre_mfa_backend"
PRE_MFA_NEXT_SESSION_KEY = "djopenkb_pre_mfa_next"


def get_totp_issuer():
    return getattr(settings, "MFA_TOTP_ISSUER", "DjOpenKB")


def get_local_mfa_backend(user=None):
    """Return the backend to use when completing local MFA login.

    AuthenticationForm normally stores the successful backend on the user
    object. If that is unavailable, fall back to the local Django backend.
    """
    backend = getattr(user, "backend", None)
    if backend:
        return backend

    for candidate in reversed(getattr(settings, "AUTHENTICATION_BACKENDS", [])):
        if "EmailOrUsernameModelBackend" in candidate or "ModelBackend" in candidate:
            return candidate

    backends = list(getattr(settings, "AUTHENTICATION_BACKENDS", []))
    return backends[-1] if backends else None


def user_requires_local_mfa(user):
    """Return True for local Django-managed accounts that must pass MFA.

    LDAP/AD users are intentionally excluded for now. Local users, staff and
    superusers are included. This makes MFA part of the local login criteria.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if not getattr(user, "is_active", False):
        return False

    profile = getattr(user, "kb_profile", None)
    if profile and getattr(profile, "is_ldap_type", False):
        return False

    # Local Django users/admins should have a usable password.
    return bool(user.has_usable_password())


def get_or_create_mfa_device(user):
    device, _created = UserMFADevice.objects.get_or_create(
        user=user,
        defaults={"secret": pyotp.random_base32()},
    )
    if not device.secret:
        device.secret = pyotp.random_base32()
        device.save(update_fields=["secret"])
    return device


def local_mfa_is_verified(request):
    """True only when the current authenticated session completed MFA."""
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return False

    return bool(
        request.session.get(LOCAL_MFA_SESSION_KEY)
        and str(request.session.get(LOCAL_MFA_USER_SESSION_KEY)) == str(user.pk)
    )


def mark_local_mfa_verified(request, user=None):
    user = user or getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return

    request.session[LOCAL_MFA_SESSION_KEY] = True
    request.session[LOCAL_MFA_USER_SESSION_KEY] = str(user.pk)
    request.session.modified = True


def clear_local_mfa_verified(request):
    request.session.pop(LOCAL_MFA_SESSION_KEY, None)
    request.session.pop(LOCAL_MFA_USER_SESSION_KEY, None)
    request.session.modified = True


def clear_pending_mfa_login(request):
    request.session.pop(PRE_MFA_USER_ID_SESSION_KEY, None)
    request.session.pop(PRE_MFA_BACKEND_SESSION_KEY, None)
    request.session.pop(PRE_MFA_NEXT_SESSION_KEY, None)
    request.session.modified = True


def begin_pending_mfa_login(request, user, next_url=None, backend=None):
    """Store a password-authenticated but MFA-incomplete local login.

    The user is deliberately not logged in yet. After TOTP setup/verification
    succeeds, complete_pending_mfa_login() creates the real Django session.
    """
    clear_local_mfa_verified(request)
    request.session[PRE_MFA_USER_ID_SESSION_KEY] = str(user.pk)
    request.session[PRE_MFA_BACKEND_SESSION_KEY] = backend or get_local_mfa_backend(user)
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

    if not user_requires_local_mfa(user):
        clear_pending_mfa_login(request)
        return None

    return user


def pending_mfa_next_url(request, default=None):
    return request.session.get(PRE_MFA_NEXT_SESSION_KEY) or default or reverse("home")


def pending_mfa_target_name(request):
    """Return mfa_setup or mfa_verify for the current pending/authenticated user."""
    user = get_pending_mfa_user(request)
    if user is None:
        user = getattr(request, "user", None)

    if not user or not getattr(user, "is_authenticated", False) and user is not None:
        # Pending user objects from get_pending_mfa_user are normal User objects
        # with is_authenticated=True as a property, so this branch is mostly for
        # anonymous requests without a pending login.
        pass

    if not user:
        return None

    device = getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed:
        return "mfa_setup"
    return "mfa_verify"


def complete_pending_mfa_login(request, user):
    """Promote the pending MFA session into a real authenticated session."""
    backend = request.session.get(PRE_MFA_BACKEND_SESSION_KEY) or get_local_mfa_backend(user)
    next_url = pending_mfa_next_url(request)

    clear_pending_mfa_login(request)

    if backend:
        auth_login(request, user, backend=backend)
    else:
        auth_login(request, user)

    mark_local_mfa_verified(request, user)
    request.session[PRE_MFA_NEXT_SESSION_KEY] = next_url
    request.session.modified = True
    return next_url


def verify_totp_code(device, code):
    code = (code or "").strip().replace(" ", "")
    if not code or not device or not device.secret:
        return False

    totp = pyotp.TOTP(device.secret)
    return bool(totp.verify(code, valid_window=1))
