"""Helpers for append-only Django Admin activity logging."""

from __future__ import annotations

from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.db import DatabaseError, OperationalError, ProgrammingError


def get_client_ip(request):
    if request is None:
        return None
    for value in (
        request.META.get("HTTP_X_REAL_IP", ""),
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0],
        request.META.get("REMOTE_ADDR", ""),
    ):
        value = (value or "").strip()
        if value:
            return value
    return None


def _safe_username(user):
    if user and getattr(user, "is_authenticated", False):
        try:
            return user.get_username()
        except Exception:
            return str(user)
    return ""


def _event_type_from_action_flag(action_flag):
    from .models import AdminActivityLog

    if action_flag == ADDITION:
        return AdminActivityLog.EventType.ADMIN_ADD
    if action_flag == CHANGE:
        return AdminActivityLog.EventType.ADMIN_CHANGE
    if action_flag == DELETION:
        return AdminActivityLog.EventType.ADMIN_DELETE
    return AdminActivityLog.EventType.ADMIN_ACTION


def log_admin_activity(
    *,
    request=None,
    event_type=None,
    admin_user=None,
    admin_username="",
    target_app_label="",
    target_model="",
    target_object_id="",
    target_repr="",
    action_flag=None,
    path="",
    request_method="",
    status_code=None,
    change_message="",
    details=None,
):
    """Append an AdminActivityLog row.

    Logging must never break the admin operation, so database errors are swallowed.
    POST values are intentionally not stored; callers should pass only metadata.
    """
    try:
        from .models import AdminActivityLog

        if request is not None:
            admin_user = admin_user or getattr(request, "user", None)
            admin_username = admin_username or _safe_username(admin_user)
            path = path or request.get_full_path()
            request_method = request_method or request.method
            user_agent = request.META.get("HTTP_USER_AGENT", "")[:2000]
            ip_address = get_client_ip(request)
        else:
            user_agent = ""
            ip_address = None

        if event_type is None:
            event_type = AdminActivityLog.EventType.ADMIN_ACTION

        AdminActivityLog.objects.create(
            event_type=event_type,
            admin_user=admin_user if getattr(admin_user, "is_authenticated", False) else None,
            admin_username=admin_username or _safe_username(admin_user),
            target_app_label=target_app_label or "",
            target_model=target_model or "",
            target_object_id=str(target_object_id or ""),
            target_repr=str(target_repr or "")[:500],
            action_flag=action_flag,
            ip_address=ip_address,
            user_agent=user_agent,
            path=path or "",
            request_method=request_method or "",
            status_code=status_code,
            change_message=change_message or "",
            details=details or {},
        )
    except (DatabaseError, OperationalError, ProgrammingError, Exception):
        return None


def log_admin_logentry(logentry):
    """Mirror Django's built-in admin LogEntry into AdminActivityLog."""
    try:
        content_type = logentry.content_type
        log_admin_activity(
            event_type=_event_type_from_action_flag(logentry.action_flag),
            admin_user=logentry.user,
            admin_username=_safe_username(logentry.user),
            target_app_label=getattr(content_type, "app_label", "") if content_type else "",
            target_model=getattr(content_type, "model", "") if content_type else "",
            target_object_id=logentry.object_id or "",
            target_repr=logentry.object_repr or "",
            action_flag=logentry.action_flag,
            change_message=logentry.get_change_message() or "",
            details={
                "source": "django_admin_logentry",
                "logentry_id": logentry.pk,
                "raw_change_message": logentry.change_message,
            },
        )
    except Exception:
        return None


def request_post_metadata(request):
    """Return safe metadata for an admin state-changing request.

    Field names are logged for traceability, but submitted values are not logged
    to avoid leaking passwords, tokens, MFA codes, CSRF tokens, or personal data.
    """
    if request is None:
        return {}
    post_keys = []
    try:
        post_keys = sorted(key for key in request.POST.keys() if key != "csrfmiddlewaretoken")
    except Exception:
        post_keys = []
    return {
        "source": "admin_request_middleware",
        "query_string": request.META.get("QUERY_STRING", ""),
        "post_field_names": post_keys[:100],
        "post_field_count": len(post_keys),
        "content_type": request.META.get("CONTENT_TYPE", ""),
    }
