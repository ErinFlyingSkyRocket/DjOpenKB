"""Small helpers for authentication and MFA monitoring logs and lockouts."""

import hashlib
import logging
import re
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q

from .models import AuthActivityLog, SiteSetting

logger = logging.getLogger(__name__)


DEFAULT_AUTH_LOCKOUT_POLICY_STAGES = [
    # failure_limit, failure_window_seconds, block_seconds, repeat_count
    {"failure_limit": 10, "failure_window_seconds": 600, "block_seconds": 300, "repeat_count": 2},
    {"failure_limit": 5, "failure_window_seconds": 600, "block_seconds": 900, "repeat_count": 2},
    {"failure_limit": 3, "failure_window_seconds": 600, "block_seconds": 1800, "repeat_count": 1},
    {"failure_limit": 3, "failure_window_seconds": 600, "block_seconds": 3600, "repeat_count": 1},
    {"failure_limit": 3, "failure_window_seconds": 600, "block_seconds": 7200, "repeat_count": 1},
    {"failure_limit": 3, "failure_window_seconds": 600, "block_seconds": 86400, "repeat_count": 0},
]


def get_client_ip(request):
    if not request:
        return None

    real_ip = (request.META.get("HTTP_X_REAL_IP") or "").strip()
    if real_ip:
        return real_ip

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        # Nginx appends the immediate client address at the right side.
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[-1]

    return request.META.get("REMOTE_ADDR") or None


def _normalize_username(username):
    username = (username or "").strip().casefold()
    return re.sub(r"\s+", "", username)[:255]


def _safe_cache_piece(value):
    value = str(value or "").strip().casefold()
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:32]


def _find_user_for_username(username):
    """Best-effort local user lookup for per-user lockout.

    Password authentication may fail before Django gives us a user object. This
    lookup lets existing local/previously-created AD users be locked by user ID,
    while unknown usernames fall back to username+IP without revealing whether
    the account exists.
    """
    normalized = _normalize_username(username)
    if not normalized:
        return None

    User = get_user_model()
    try:
        return (
            User.objects.filter(Q(username__iexact=normalized) | Q(email__iexact=normalized))
            .only("id", "username", "email")
            .first()
        )
    except Exception:
        logger.exception("Unable to look up user for authentication lockout")
        return None


def get_auth_lockout_identifier(request=None, username="", user=None, purpose="password"):
    """Return a stable lockout identifier for password/MFA checks.

    MFA/profile-sensitive checks use user ID whenever available. Password checks
    use user ID for existing users and username+IP for unknown users so one
    attacker cannot lock every possible account from a single shared IP.
    """
    purpose = (purpose or "password").strip().lower() or "password"
    ip = get_client_ip(request) or "unknown"

    if user and getattr(user, "pk", None):
        return f"{purpose}:user:{user.pk}"

    found_user = _find_user_for_username(username)
    if found_user and getattr(found_user, "pk", None):
        return f"{purpose}:user:{found_user.pk}"

    normalized = _normalize_username(username) or "blank"
    return f"{purpose}:username_ip:{_safe_cache_piece(normalized)}:{_safe_cache_piece(ip)}"


def _lockout_keys(identifier):
    safe_identifier = _safe_cache_piece(identifier)
    return {
        "failures": f"auth_lockout:failures:{safe_identifier}",
        "block": f"auth_lockout:block:{safe_identifier}",
        "strikes": f"auth_lockout:strikes:{safe_identifier}",
    }


def _positive_int(value, default, minimum=1, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _env_fallback_policy_stages():
    """Fallback policy for first boot before the DB setting/table exists."""
    failure_limit = _positive_int(getattr(settings, "AUTH_LOCKOUT_FAILURE_LIMIT", 10), 10, minimum=1)
    window_seconds = _positive_int(getattr(settings, "AUTH_LOCKOUT_WINDOW_SECONDS", 600), 600, minimum=60)
    durations = getattr(settings, "AUTH_LOCKOUT_DURATIONS_SECONDS", [300, 900, 3600, 86400])
    clean_durations = []
    for duration in durations:
        value = _positive_int(duration, 0, minimum=0)
        if value > 0:
            clean_durations.append(value)
    if not clean_durations:
        clean_durations = [300, 900, 3600, 86400]

    stages = []
    for index, duration in enumerate(clean_durations):
        stages.append(
            {
                "failure_limit": failure_limit,
                "failure_window_seconds": window_seconds,
                "block_seconds": duration,
                # Old env fallback escalates once per duration, then repeats the last duration.
                "repeat_count": 0 if index == len(clean_durations) - 1 else 1,
                "source": "env_fallback",
                "stage_number": index + 1,
            }
        )
    return stages


def get_auth_lockout_policy_stages():
    """Return enabled progressive lockout stages from Site settings.

    Admins edit these as inline rows on the singleton SiteSetting object. If the
    database is not ready yet, or all rows are disabled/deleted, fall back to the
    environment-based settings so authentication never breaks during deployment.
    """
    try:
        setting = SiteSetting.load()
        stages = []
        for index, row in enumerate(setting.auth_lockout_stages.filter(enabled=True).order_by("sort_order", "id"), start=1):
            failure_limit = _positive_int(row.failure_limit, 10, minimum=1, maximum=200)
            failure_window_seconds = _positive_int(row.failure_window_seconds, 600, minimum=60, maximum=604800)
            block_seconds = _positive_int(row.block_seconds, 300, minimum=60, maximum=2592000)
            repeat_count = _positive_int(row.repeat_count, 1, minimum=0, maximum=1000)
            stages.append(
                {
                    "failure_limit": failure_limit,
                    "failure_window_seconds": failure_window_seconds,
                    "block_seconds": block_seconds,
                    "repeat_count": repeat_count,
                    "source": "site_setting",
                    "stage_number": index,
                    "stage_id": row.pk,
                    "sort_order": row.sort_order,
                }
            )
        if stages:
            return stages
    except Exception:
        logger.exception("Unable to load authentication lockout policy stages; using env fallback policy")

    return _env_fallback_policy_stages()


def _stage_for_strike_count(strikes_so_far):
    """Pick the policy stage for the next block.

    strikes_so_far is the number of previous lockouts for this password/MFA
    identifier since the last successful verification/reset.
    """
    try:
        strike_index = max(0, int(strikes_so_far))
    except (TypeError, ValueError):
        strike_index = 0

    stages = get_auth_lockout_policy_stages()
    consumed = 0
    for stage in stages:
        repeat_count = _positive_int(stage.get("repeat_count"), 1, minimum=0)
        if repeat_count == 0:
            return stage
        if strike_index < consumed + repeat_count:
            return stage
        consumed += repeat_count

    # If no stage is marked forever, repeat the last enabled row.
    return stages[-1]


def _get_strike_ttl_seconds(stage=None):
    env_ttl = _positive_int(getattr(settings, "AUTH_LOCKOUT_STRIKE_TTL_SECONDS", 86400), 86400, minimum=3600)
    site_ttl = env_ttl
    try:
        site_ttl = _positive_int(SiteSetting.load().auth_lockout_strike_ttl_seconds, 604800, minimum=3600)
    except Exception:
        site_ttl = env_ttl

    block_seconds = _positive_int((stage or {}).get("block_seconds"), 0, minimum=0)
    return max(env_ttl, site_ttl, block_seconds + 3600)


def get_auth_lockout_status(request=None, username="", user=None, purpose="password"):
    """Return (is_locked, retry_after_seconds, identifier)."""
    identifier = get_auth_lockout_identifier(request=request, username=username, user=user, purpose=purpose)
    keys = _lockout_keys(identifier)
    blocked_until = cache.get(keys["block"])
    if blocked_until:
        now = int(time.time())
        retry_after = max(1, int(blocked_until) - now)
        return True, retry_after, identifier
    return False, 0, identifier


def record_auth_failure(request=None, username="", user=None, purpose="password"):
    """Record a failed password/MFA attempt and return lockout state.

    Returns a dict with: locked, retry_after_seconds, identifier, failure_count,
    failure_limit, block_seconds, policy_stage.
    """
    locked, retry_after, identifier = get_auth_lockout_status(
        request=request,
        username=username,
        user=user,
        purpose=purpose,
    )
    keys = _lockout_keys(identifier)
    strikes_so_far = cache.get(keys["strikes"]) or 0
    try:
        strikes_so_far = int(strikes_so_far)
    except (TypeError, ValueError):
        strikes_so_far = 0

    stage = _stage_for_strike_count(strikes_so_far)
    failure_limit = _positive_int(stage.get("failure_limit"), 10, minimum=1)
    window_seconds = _positive_int(stage.get("failure_window_seconds"), 600, minimum=60)

    if locked:
        return {
            "locked": True,
            "retry_after_seconds": retry_after,
            "identifier": identifier,
            "failure_count": cache.get(keys["failures"]) or failure_limit,
            "failure_limit": failure_limit,
            "block_seconds": retry_after,
            "policy_stage": stage,
            "strikes_so_far": strikes_so_far,
        }

    failures = cache.get(keys["failures"]) or 0
    try:
        failures = int(failures) + 1
    except (TypeError, ValueError):
        failures = 1
    cache.set(keys["failures"], failures, window_seconds)

    block_seconds = 0
    if failures >= failure_limit:
        block_seconds = _positive_int(stage.get("block_seconds"), 300, minimum=60)
        blocked_until = int(time.time()) + block_seconds
        strikes_now = strikes_so_far + 1
        cache.set(keys["strikes"], strikes_now, _get_strike_ttl_seconds(stage))
        cache.set(keys["block"], blocked_until, block_seconds)
        cache.delete(keys["failures"])
        locked = True
        retry_after = block_seconds

    return {
        "locked": bool(locked),
        "retry_after_seconds": int(retry_after),
        "identifier": identifier,
        "failure_count": failures,
        "failure_limit": failure_limit,
        "block_seconds": block_seconds,
        "policy_stage": stage,
        "strikes_so_far": strikes_so_far,
    }


def reset_auth_lockout(identifier):
    """Clear failures, active block, and escalation strikes for one identifier."""
    if not identifier:
        return 0
    keys = _lockout_keys(identifier)
    deleted = cache.delete_many([keys["failures"], keys["block"], keys["strikes"]])
    # Some cache backends return None for delete_many.
    return int(deleted or 0)


def reset_user_auth_lockouts(user):
    """Clear password and MFA lockouts for a known user."""
    if not user or not getattr(user, "pk", None):
        return []

    identifiers = [
        get_auth_lockout_identifier(user=user, purpose="password"),
        get_auth_lockout_identifier(user=user, purpose="mfa"),
    ]
    for identifier in identifiers:
        reset_auth_lockout(identifier)
    return identifiers


def record_auth_success(request=None, username="", user=None, purpose="password"):
    """Clear failure counters, active blocks, and escalation strikes after success."""
    identifier = get_auth_lockout_identifier(request=request, username=username, user=user, purpose=purpose)
    reset_auth_lockout(identifier)
    return identifier


def format_retry_after(seconds):
    try:
        seconds = max(1, int(seconds))
    except (TypeError, ValueError):
        seconds = 60

    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"

    minutes = (seconds + 59) // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    hours = (minutes + 59) // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"

    days = (hours + 23) // 24
    return f"{days} day{'s' if days != 1 else ''}"


def log_auth_event(request=None, event_type="", success=False, user=None, username="", login_mode="", details=None):
    """Record a best-effort auth/MFA audit event without breaking login flows."""
    if not event_type:
        return None

    try:
        if user and not username:
            username = user.get_username()

        if request is not None:
            username = username or (request.POST.get("username") or request.POST.get("email") or "").strip()
            login_mode = login_mode or (request.POST.get("login_mode") or request.GET.get("login_mode") or "").strip().lower()

        return AuthActivityLog.objects.create(
            event_type=event_type,
            success=bool(success),
            user=user if getattr(user, "pk", None) else None,
            username=(username or "")[:255],
            login_mode=(login_mode or "")[:30],
            ip_address=get_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else ""),
            path=(request.get_full_path()[:500] if request else ""),
            request_method=(request.method[:10] if request else ""),
            details=details or {},
        )
    except Exception:
        logger.exception("Unable to write authentication activity log for event_type=%s", event_type)
        return None
