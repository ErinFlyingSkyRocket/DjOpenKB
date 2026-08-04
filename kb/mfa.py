import hashlib
import math
import secrets

import pyotp
from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.sessions.models import Session
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.crypto import constant_time_compare
from django.utils.translation import gettext_lazy as _

from .crypto import decrypt_value, encrypt_value
from .models import SiteSetting, UserMFADevice


MFA_SESSION_KEY = "djopenkb_mfa_verified"
MFA_USER_SESSION_KEY = "djopenkb_mfa_verified_user_id"

PRE_MFA_USER_ID_SESSION_KEY = "djopenkb_pre_mfa_user_id"
PRE_MFA_BACKEND_SESSION_KEY = "djopenkb_pre_mfa_backend"
PRE_MFA_NEXT_SESSION_KEY = "djopenkb_pre_mfa_next"
PRE_MFA_STARTED_AT_SESSION_KEY = "djopenkb_pre_mfa_started_at"
PRE_MFA_EXPIRES_AT_SESSION_KEY = "djopenkb_pre_mfa_expires_at"
PRE_MFA_CHALLENGE_ID_SESSION_KEY = "djopenkb_pre_mfa_challenge_id"
PRE_MFA_LOCKOUT_DEFERRED_SESSION_KEY = "djopenkb_pre_mfa_lockout_deferred"

PENDING_MFA_RESET_USER_ID_SESSION_KEY = "djopenkb_pending_mfa_reset_user_id"
PENDING_MFA_RESET_SECRET_SESSION_KEY = "djopenkb_pending_mfa_reset_secret"
PENDING_MFA_RESET_CHALLENGE_ID_SESSION_KEY = "djopenkb_pending_mfa_reset_challenge_id"
PENDING_MFA_RESET_EXPIRES_AT_SESSION_KEY = "djopenkb_pending_mfa_reset_expires_at"
PENDING_MFA_RESET_DEVICE_FINGERPRINT_SESSION_KEY = "djopenkb_pending_mfa_reset_device_fingerprint"
PENDING_MFA_RESET_AUTH_HASH_SESSION_KEY = "djopenkb_pending_mfa_reset_auth_hash"

MFA_RESET_SETUP_TIMEOUT_SECONDS = 10 * 60

MFA_LOGIN_TIMEOUT_DEFAULT_SECONDS = 60
MFA_LOGIN_TIMEOUT_MIN_SECONDS = 30
MFA_LOGIN_TIMEOUT_MAX_SECONDS = 900

# Backwards-compatible names from the earlier local-only MFA implementation.
LOCAL_MFA_SESSION_KEY = MFA_SESSION_KEY
LOCAL_MFA_USER_SESSION_KEY = MFA_USER_SESSION_KEY


def get_totp_issuer():
    return getattr(settings, "MFA_TOTP_ISSUER", "Knowledge Repository")


def get_totp_valid_window():
    """Return the allowed TOTP drift window, clamped to a safe range.

    Each window is 30 seconds. A value of 1 accepts the current code plus
    one window before/after the current server time. This helps with small
    host/phone clock drift, but it should not replace proper NTP.
    """
    try:
        return max(0, min(int(getattr(settings, "MFA_TOTP_VALID_WINDOW", 1)), 3))
    except (TypeError, ValueError):
        return 1


def get_mfa_login_timeout_seconds():
    """Return the admin-configured password-to-MFA completion deadline.

    The database-backed Site setting is authoritative after migrations are
    available. A settings fallback keeps startup and migration commands safe.
    Values are clamped to 30 seconds through 15 minutes.
    """
    fallback = getattr(settings, "MFA_LOGIN_TIMEOUT_SECONDS", MFA_LOGIN_TIMEOUT_DEFAULT_SECONDS)
    try:
        value = SiteSetting.load().mfa_login_timeout_seconds
    except Exception:
        value = fallback

    try:
        value = int(value)
    except (TypeError, ValueError):
        value = MFA_LOGIN_TIMEOUT_DEFAULT_SECONDS

    return min(max(value, MFA_LOGIN_TIMEOUT_MIN_SECONDS), MFA_LOGIN_TIMEOUT_MAX_SECONDS)


def _pending_mfa_session_datetime(request, key):
    raw_value = request.session.get(key)
    if not raw_value:
        return None

    parsed_value = parse_datetime(str(raw_value))
    if parsed_value is None:
        return None
    if timezone.is_naive(parsed_value):
        parsed_value = timezone.make_aware(
            parsed_value,
            timezone.get_current_timezone(),
        )
    return parsed_value


def _pending_mfa_started_at(request):
    return _pending_mfa_session_datetime(request, PRE_MFA_STARTED_AT_SESSION_KEY)


def _pending_mfa_expires_at(request):
    return _pending_mfa_session_datetime(request, PRE_MFA_EXPIRES_AT_SESSION_KEY)


def pending_mfa_deadline_is_deferred(request):
    """Return whether an active MFA cooldown is intentionally pausing the timer."""
    return bool(request.session.get(PRE_MFA_LOCKOUT_DEFERRED_SESSION_KEY))


def ensure_pending_mfa_deadline(request):
    """Return the fixed password-to-MFA deadline for the pending login.

    The timer is deliberately not started while an MFA rate-limit cooldown is
    active. Once the cooldown ends, the view calls this helper to begin one new
    fixed completion window. Changing Site settings cannot extend a deadline
    that has already started.
    """
    if not request.session.get(PRE_MFA_USER_ID_SESSION_KEY):
        return None
    if pending_mfa_deadline_is_deferred(request):
        return None

    deadline_changed = False

    started_at = _pending_mfa_started_at(request)
    if started_at is None:
        started_at = timezone.now()
        request.session[PRE_MFA_STARTED_AT_SESSION_KEY] = started_at.isoformat()
        deadline_changed = True

    expires_at = _pending_mfa_expires_at(request)
    if expires_at is None or expires_at <= started_at:
        expires_at = started_at + timezone.timedelta(
            seconds=get_mfa_login_timeout_seconds()
        )
        request.session[PRE_MFA_EXPIRES_AT_SESSION_KEY] = expires_at.isoformat()
        deadline_changed = True

    if deadline_changed:
        request.session.modified = True
    return expires_at


def defer_pending_mfa_deadline_for_lockout(request):
    """Pause the MFA completion window while the server-side cooldown is active.

    The pending password-authenticated identity remains in the session, but the
    60-second verification deadline does not run underneath a longer lockout.
    Rotating the challenge once invalidates any pre-lockout browser tab without
    repeatedly changing it on every locked-page refresh.
    """
    if not request.session.get(PRE_MFA_USER_ID_SESSION_KEY):
        return

    already_deferred = pending_mfa_deadline_is_deferred(request)
    had_deadline = bool(
        request.session.get(PRE_MFA_STARTED_AT_SESSION_KEY)
        or request.session.get(PRE_MFA_EXPIRES_AT_SESSION_KEY)
    )
    request.session.pop(PRE_MFA_STARTED_AT_SESSION_KEY, None)
    request.session.pop(PRE_MFA_EXPIRES_AT_SESSION_KEY, None)
    if not already_deferred or had_deadline:
        request.session[PRE_MFA_CHALLENGE_ID_SESSION_KEY] = secrets.token_urlsafe(32)
    request.session[PRE_MFA_LOCKOUT_DEFERRED_SESSION_KEY] = True
    request.session.modified = True


def resume_pending_mfa_deadline_after_lockout(request):
    """Start a fresh fixed MFA window after the active cooldown has ended."""
    if not request.session.get(PRE_MFA_USER_ID_SESSION_KEY):
        return None
    if pending_mfa_deadline_is_deferred(request):
        request.session.pop(PRE_MFA_LOCKOUT_DEFERRED_SESSION_KEY, None)
        request.session.modified = True
    return ensure_pending_mfa_deadline(request)


def ensure_pending_mfa_started_at(request):
    """Backwards-compatible helper that ensures the fixed deadline exists."""
    expires_at = ensure_pending_mfa_deadline(request)
    if expires_at is None:
        return None
    return _pending_mfa_started_at(request)


def pending_mfa_seconds_remaining(request, *, start_if_missing=True):
    """Return whole seconds left to finish MFA, or None while it is deferred."""
    if start_if_missing:
        expires_at = ensure_pending_mfa_deadline(request)
    else:
        expires_at = _pending_mfa_expires_at(request)
    if expires_at is None:
        return None

    remaining = math.ceil((expires_at - timezone.now()).total_seconds())
    return max(0, remaining)


def pending_mfa_login_has_expired(request):
    """Check only an already-started deadline; never start one as a side effect."""
    remaining = pending_mfa_seconds_remaining(request, start_if_missing=False)
    return remaining is not None and remaining <= 0


def mfa_device_secret_is_readable(device):
    """Return True when an MFA device has a decryptable TOTP secret.

    If this is False for a confirmed device, the field encryption key or
    DJANGO_SECRET_KEY likely changed after the device was created. The safe
    recovery is to reset MFA for that user and have them scan a new QR code.
    """
    return bool(device and device.get_secret())


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

    secret_is_readable = bool(device.get_secret())
    if not secret_is_readable and not device.confirmed:
        # An unconfirmed setup may safely receive a fresh secret because the
        # user will see the replacement QR/manual key before confirming it.
        device.set_secret(pyotp.random_base32())
        device.save(update_fields=["secret"])

    # Never silently replace an unreadable secret on a confirmed device. The
    # user would have no copy of that newly generated secret and would be
    # permanently unable to verify it. Callers deliberately detect this state
    # with mfa_device_secret_is_readable() and direct the user/admin to reset MFA.
    return device


def mfa_status_label(user):
    device = getattr(user, "kb_mfa_device", None)
    if not user_requires_mfa(user):
        return _("Not required")
    if not device:
        return _("Not set up")
    if device.confirmed:
        return _("Configured")
    return _("Setup pending")


def reset_mfa_device_for_user(user):
    """Generate a fresh private TOTP secret and require setup again.

    This immediate reset is reserved for administrative recovery. Self-service
    replacement uses a staged secret and keeps the current confirmed device
    active until the user verifies the new authenticator code.
    """
    device = get_or_create_mfa_device(user)
    now = timezone.now()
    device.set_secret(pyotp.random_base32())
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


def _mfa_device_secret_fingerprint(device):
    """Return a non-secret version marker for the currently stored device."""
    stored_value = str(getattr(device, "secret", "") or "")
    return hashlib.sha256(stored_value.encode("utf-8")).hexdigest()


def clear_pending_mfa_reset(request):
    """Remove a staged self-service MFA replacement from this session."""
    changed = False
    for key in (
        PENDING_MFA_RESET_USER_ID_SESSION_KEY,
        PENDING_MFA_RESET_SECRET_SESSION_KEY,
        PENDING_MFA_RESET_CHALLENGE_ID_SESSION_KEY,
        PENDING_MFA_RESET_EXPIRES_AT_SESSION_KEY,
        PENDING_MFA_RESET_DEVICE_FINGERPRINT_SESSION_KEY,
        PENDING_MFA_RESET_AUTH_HASH_SESSION_KEY,
    ):
        if key in request.session:
            request.session.pop(key, None)
            changed = True
    if changed:
        request.session.modified = True


def begin_pending_mfa_reset(request, user, device=None):
    """Stage a new encrypted TOTP secret without changing the active device."""
    device = device or getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed or not mfa_device_secret_is_readable(device):
        return None

    clear_pending_mfa_reset(request)
    raw_secret = pyotp.random_base32()
    expires_at = timezone.now() + timezone.timedelta(seconds=MFA_RESET_SETUP_TIMEOUT_SECONDS)
    request.session[PENDING_MFA_RESET_USER_ID_SESSION_KEY] = str(user.pk)
    request.session[PENDING_MFA_RESET_SECRET_SESSION_KEY] = encrypt_value(raw_secret)
    request.session[PENDING_MFA_RESET_CHALLENGE_ID_SESSION_KEY] = secrets.token_urlsafe(32)
    request.session[PENDING_MFA_RESET_EXPIRES_AT_SESSION_KEY] = expires_at.isoformat()
    request.session[PENDING_MFA_RESET_DEVICE_FINGERPRINT_SESSION_KEY] = _mfa_device_secret_fingerprint(device)
    request.session[PENDING_MFA_RESET_AUTH_HASH_SESSION_KEY] = user.get_session_auth_hash()
    request.session.modified = True
    return raw_secret


def get_pending_mfa_reset(request, user=None):
    """Return the valid staged reset state for this user and session."""
    user = user or getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        clear_pending_mfa_reset(request)
        return None

    if not constant_time_compare(
        str(request.session.get(PENDING_MFA_RESET_USER_ID_SESSION_KEY, "")),
        str(user.pk),
    ):
        clear_pending_mfa_reset(request)
        return None

    stored_auth_hash = str(
        request.session.get(PENDING_MFA_RESET_AUTH_HASH_SESSION_KEY, "") or ""
    )
    if not stored_auth_hash or not constant_time_compare(
        stored_auth_hash,
        user.get_session_auth_hash(),
    ):
        clear_pending_mfa_reset(request)
        return None

    expires_at = _pending_mfa_session_datetime(
        request,
        PENDING_MFA_RESET_EXPIRES_AT_SESSION_KEY,
    )
    if expires_at is None or expires_at <= timezone.now():
        clear_pending_mfa_reset(request)
        return None

    secret = decrypt_value(request.session.get(PENDING_MFA_RESET_SECRET_SESSION_KEY, ""))
    challenge_id = str(request.session.get(PENDING_MFA_RESET_CHALLENGE_ID_SESSION_KEY, "") or "")
    device_fingerprint = str(
        request.session.get(PENDING_MFA_RESET_DEVICE_FINGERPRINT_SESSION_KEY, "") or ""
    )
    if not secret or not challenge_id or not device_fingerprint:
        clear_pending_mfa_reset(request)
        return None

    return {
        "secret": secret,
        "challenge_id": challenge_id,
        "expires_at": expires_at,
        "device_fingerprint": device_fingerprint,
    }


def pending_mfa_reset_challenge_matches(request, user, submitted_challenge_id):
    state = get_pending_mfa_reset(request, user)
    submitted = str(submitted_challenge_id or "").strip()
    return bool(
        state
        and submitted
        and secrets.compare_digest(state["challenge_id"], submitted)
    )


def pending_mfa_reset_device_matches(request, user, device=None):
    """Prevent an older staged flow from overwriting a newer MFA device."""
    state = get_pending_mfa_reset(request, user)
    device = device or getattr(user, "kb_mfa_device", None)
    return bool(
        state
        and device
        and secrets.compare_digest(
            state["device_fingerprint"],
            _mfa_device_secret_fingerprint(device),
        )
    )


def clear_user_auth_sessions(user, *, exclude_session_key=None):
    """Delete active sessions for a user after an MFA secret replacement.

    The self-service flow may preserve the initiating session after it has
    verified both the old and new factors. Administrative resets omit the
    exclusion and continue invalidating every active or pending session.
    """
    deleted = 0
    user_id = str(user.pk)
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if exclude_session_key and session.session_key == exclude_session_key:
            continue
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
    request.session.pop(PRE_MFA_STARTED_AT_SESSION_KEY, None)
    request.session.pop(PRE_MFA_EXPIRES_AT_SESSION_KEY, None)
    request.session.pop(PRE_MFA_CHALLENGE_ID_SESSION_KEY, None)
    request.session.pop(PRE_MFA_LOCKOUT_DEFERRED_SESSION_KEY, None)
    request.session.modified = True


def begin_pending_mfa_login(request, user, next_url=None, backend=None):
    """Store one fresh password-authenticated, MFA-incomplete challenge.

    The real Django authenticated session is created only after TOTP
    setup/verification succeeds. Starting a newer password-authenticated login
    replaces the older pending challenge so stale browser tabs cannot submit,
    cancel, or expire the latest attempt.
    """
    clear_mfa_verified(request)
    clear_pending_mfa_login(request)

    request.session[PRE_MFA_USER_ID_SESSION_KEY] = str(user.pk)
    request.session[PRE_MFA_BACKEND_SESSION_KEY] = backend or get_mfa_completion_backend(user)
    request.session[PRE_MFA_NEXT_SESSION_KEY] = next_url or reverse("home")
    request.session[PRE_MFA_CHALLENGE_ID_SESSION_KEY] = secrets.token_urlsafe(32)

    # A longer active MFA cooldown takes precedence over the short completion
    # window. Keep the password-authenticated pending state, but start the fixed
    # timer only after the server-side lockout has ended.
    locked = False
    try:
        from .auth_monitoring import get_auth_lockout_status

        locked, _retry_after, _identifier = get_auth_lockout_status(
            request,
            user=user,
            purpose="mfa",
        )
    except Exception:
        locked = False

    if locked:
        request.session[PRE_MFA_LOCKOUT_DEFERRED_SESSION_KEY] = True
    else:
        started_at = timezone.now()
        expires_at = started_at + timezone.timedelta(
            seconds=get_mfa_login_timeout_seconds()
        )
        request.session[PRE_MFA_STARTED_AT_SESSION_KEY] = started_at.isoformat()
        request.session[PRE_MFA_EXPIRES_AT_SESSION_KEY] = expires_at.isoformat()
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


def start_disabled_account_session(request, user):
    """Create a restricted authenticated session for the disabled-account page.

    Disabled users must not be allowed into normal Knowledge Repository functions, but the
    account-disabled page is intentionally authenticated-only so anonymous users
    cannot browse to it. This helper promotes a verified password/MFA flow into
    a restricted session. DisabledUserLogoutMiddleware then allows only the
    disabled-account page and logout, and redirects every other request back to
    the disabled page before the requested function can run.
    """
    backend = request.session.get(PRE_MFA_BACKEND_SESSION_KEY) or get_mfa_completion_backend(user)

    clear_pending_mfa_login(request)
    clear_mfa_verified(request)

    if backend:
        auth_login(request, user, backend=backend)
    else:
        auth_login(request, user)

    request.session["djopenkb_disabled_account_session"] = True
    request.session.modified = True


def pending_mfa_challenge_id(request):
    value = request.session.get(PRE_MFA_CHALLENGE_ID_SESSION_KEY)
    return value if isinstance(value, str) and value else None


def ensure_pending_mfa_challenge_id(request):
    """Return the current challenge ID without changing its fixed deadline."""
    if not request.session.get(PRE_MFA_USER_ID_SESSION_KEY):
        return None

    challenge_id = pending_mfa_challenge_id(request)
    if challenge_id is None:
        # Backwards compatibility for a pending session created before this
        # update. The timer remains unchanged; only the missing ID is added.
        challenge_id = secrets.token_urlsafe(32)
        request.session[PRE_MFA_CHALLENGE_ID_SESSION_KEY] = challenge_id
        request.session.modified = True
    return challenge_id


def pending_mfa_challenge_matches(request, submitted_challenge_id):
    current = ensure_pending_mfa_challenge_id(request)
    submitted = (submitted_challenge_id or "").strip()
    return bool(current and submitted and secrets.compare_digest(current, submitted))


def verify_totp_secret(secret, code):
    code = (code or "").strip().replace(" ", "")
    if not code or not secret:
        return False

    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code, valid_window=get_totp_valid_window()))


def verify_totp_code(device, code):
    if not device:
        return False
    return verify_totp_secret(device.get_secret(), code)
