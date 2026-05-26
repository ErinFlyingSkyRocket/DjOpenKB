"""Small helpers for authentication and MFA monitoring logs."""

import logging

from .models import AuthActivityLog

logger = logging.getLogger(__name__)


def get_client_ip(request):
    if not request:
        return None

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or None

    return request.META.get("REMOTE_ADDR") or None


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
