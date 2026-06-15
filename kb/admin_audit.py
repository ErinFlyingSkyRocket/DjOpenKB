"""Helpers for append-only Django Admin activity logging."""

from __future__ import annotations

import json
import re

from django.apps import apps
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.db import DatabaseError, OperationalError, ProgrammingError
from django.utils.text import capfirst



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


def _model_label(app_label: str, model_name: str) -> str:
    """Return a friendly model label for admin audit displays."""
    if not app_label or not model_name:
        return ""
    try:
        model = apps.get_model(app_label, model_name)
        return str(model._meta.verbose_name)
    except Exception:
        return f"{app_label}.{model_name}".strip(".")


def _resolve_object_repr(app_label: str, model_name: str, object_id: str) -> str:
    """Resolve an admin URL object ID into the object's current display name."""
    if not app_label or not model_name or not object_id:
        return ""
    try:
        model = apps.get_model(app_label, model_name)
        obj = model._default_manager.filter(pk=object_id).first()
        return str(obj)[:500] if obj is not None else ""
    except Exception:
        return ""


def _admin_url_target_from_path(path: str) -> dict:
    """Infer app/model/object information from common Django Admin URLs."""
    match = re.match(r"^/admin/(?P<app>[^/]+)/(?P<model>[^/]+)(?:/(?P<object_id>[^/]+))?/?", path or "")
    if not match:
        return {}

    app_label = match.group("app") or ""
    model_name = match.group("model") or ""
    object_id = match.group("object_id") or ""
    if object_id in {"add", "history", "delete"}:
        object_id = ""

    target_repr = _resolve_object_repr(app_label, model_name, object_id)
    return {
        "target_app_label": app_label,
        "target_model": model_name,
        "target_object_id": object_id,
        "target_repr": target_repr,
    }


def _selected_admin_objects(request, app_label: str, model_name: str) -> tuple[list[str], int]:
    """Return safe display names for selected changelist action objects."""
    try:
        selected_ids = [value for value in request.POST.getlist("_selected_action") if value]
    except Exception:
        selected_ids = []

    if not selected_ids or not app_label or not model_name:
        return [], len(selected_ids)

    try:
        model = apps.get_model(app_label, model_name)
        objects = model._default_manager.filter(pk__in=selected_ids[:20])
        names = [str(obj)[:120] for obj in objects]
        return names, len(selected_ids)
    except Exception:
        return [], len(selected_ids)


def _human_status(status_code) -> str:
    try:
        status_code = int(status_code)
    except Exception:
        return ""
    if 200 <= status_code < 300:
        return "successful"
    if 300 <= status_code < 400:
        return "redirected"
    if status_code in {401, 403}:
        return "denied"
    if status_code == 404:
        return "not found"
    if status_code >= 500:
        return "server error"
    if status_code >= 400:
        return "failed"
    return ""


def build_admin_action_label(*, event_type=None, target_label="", target_repr="", action_flag=None, status_code=None, change_message="", details=None) -> str:
    """Create a clear one-line admin audit sentence."""
    details = details or {}
    explicit = details.get("action_label") or details.get("summary")
    if explicit:
        return str(explicit)

    action_name = details.get("admin_action") or details.get("action")
    status_text = _human_status(status_code)
    target = target_repr or details.get("target_display") or target_label or "admin area"

    from .models import AdminActivityLog

    if event_type == AdminActivityLog.EventType.ADMIN_ADD:
        return f"Created {target_label or 'object'}: {target}" if target_label else f"Created {target}"
    if event_type == AdminActivityLog.EventType.ADMIN_CHANGE:
        suffix = f" — {change_message}" if change_message else ""
        return f"Changed {target_label or 'object'}: {target}{suffix}"
    if event_type == AdminActivityLog.EventType.ADMIN_DELETE:
        return f"Deleted {target_label or 'object'}: {target}" if target_label else f"Deleted {target}"
    if action_name:
        prefix = f"Ran admin action '{action_name}'"
        if target != "admin area":
            prefix += f" on {target}"
        if status_text:
            prefix += f" ({status_text})"
        return prefix
    if status_text == "denied":
        return f"Admin request denied for {target}"
    if status_text == "server error":
        return f"Admin request failed for {target}"
    if status_text:
        return f"Admin request {status_text} for {target}"
    return f"Admin request for {target}"



SENSITIVE_FIELD_NAME_TOKENS = (
    "password",
    "secret",
    "token",
    "key",
    "credential",
    "csrf",
    "otp",
    "mfa",
    "api",
)


def is_sensitive_admin_field(field_name: str) -> bool:
    """Return True when a field value should not be written to audit logs."""
    value = (field_name or "").lower()
    return any(token in value for token in SENSITIVE_FIELD_NAME_TOKENS)


def _truncate(value, limit=180):
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _display_scalar(value):
    if value is None:
        return "-"
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return _truncate(value)


def _format_field_value(obj, field):
    """Return a safe, readable value for a concrete model field."""
    name = getattr(field, "name", "")
    if is_sensitive_admin_field(name):
        raw_value = getattr(obj, name, None)
        return "set/changed (hidden)" if raw_value else "not set"

    try:
        raw_value = getattr(obj, name)
    except Exception:
        return "-"

    if getattr(field, "is_relation", False) and getattr(field, "many_to_one", False):
        try:
            return _truncate(raw_value) if raw_value is not None else "-"
        except Exception:
            return _display_scalar(getattr(obj, f"{name}_id", None))

    try:
        choices = getattr(field, "choices", None)
        if choices:
            display = getattr(obj, f"get_{name}_display", None)
            if callable(display):
                return _truncate(display())
    except Exception:
        pass

    return _display_scalar(raw_value)


def _format_many_to_many_values(obj, field):
    name = getattr(field, "name", "")
    if is_sensitive_admin_field(name):
        return []
    if not getattr(obj, "pk", None):
        return []
    try:
        manager = getattr(obj, name)
        return sorted(_truncate(item, 120) for item in manager.all())
    except Exception:
        return []


def build_admin_object_snapshot(obj, *, extra=None):
    """Return safe field/membership state for before/after admin diffing."""
    if obj is None:
        return {}

    snapshot = {}
    try:
        for field in obj._meta.fields:
            name = getattr(field, "name", "")
            if name == "id":
                continue
            label = str(getattr(field, "verbose_name", name)).strip().capitalize()
            snapshot[name] = {
                "label": label or name,
                "value": _format_field_value(obj, field),
                "kind": "field",
            }

        for field in obj._meta.many_to_many:
            name = getattr(field, "name", "")
            label = str(getattr(field, "verbose_name", name)).strip().capitalize()
            snapshot[name] = {
                "label": label or name,
                "value": _format_many_to_many_values(obj, field),
                "kind": "m2m",
            }
    except Exception:
        pass

    for key, item in (extra or {}).items():
        label = item.get("label") or key.replace("_", " ").title()
        value = item.get("value")
        kind = item.get("kind") or ("m2m" if isinstance(value, (list, tuple, set)) else "field")
        if isinstance(value, set):
            value = sorted(value)
        elif isinstance(value, tuple):
            value = list(value)
        snapshot[key] = {
            "label": str(label),
            "value": value,
            "kind": kind,
        }

    return snapshot


def _value_equal(left, right):
    if isinstance(left, list) or isinstance(right, list):
        return sorted(left or []) == sorted(right or [])
    return str(left) == str(right)


def build_admin_change_entries(before, after):
    """Build concise changed-field entries from two safe snapshots."""
    before = before or {}
    after = after or {}
    entries = []
    for key in sorted(set(before) | set(after)):
        before_item = before.get(key) or {}
        after_item = after.get(key) or {}
        label = after_item.get("label") or before_item.get("label") or key
        kind = after_item.get("kind") or before_item.get("kind") or "field"
        old_value = before_item.get("value")
        new_value = after_item.get("value")
        if _value_equal(old_value, new_value):
            continue

        if kind == "m2m":
            old_values = set(old_value or [])
            new_values = set(new_value or [])
            added = sorted(new_values - old_values)
            removed = sorted(old_values - new_values)
            if not added and not removed:
                continue
            entries.append({
                "field": key,
                "label": label,
                "kind": "membership",
                "added": added[:50],
                "removed": removed[:50],
                "added_count": len(added),
                "removed_count": len(removed),
            })
            continue

        entries.append({
            "field": key,
            "label": label,
            "kind": "field",
            "from": old_value,
            "to": new_value,
        })
    return entries


def describe_admin_change_entries(entries, *, limit=8):
    """Turn changed-field entries into a readable one-line sentence fragment."""
    parts = []
    for entry in (entries or [])[:limit]:
        label = entry.get("label") or entry.get("field") or "Field"
        if entry.get("kind") == "membership":
            subparts = []
            added = entry.get("added") or []
            removed = entry.get("removed") or []
            if added:
                extra = int(entry.get("added_count") or len(added)) - len(added)
                text = ", ".join(added[:8])
                if extra > 0:
                    text += f", +{extra} more"
                subparts.append(f"added {text}")
            if removed:
                extra = int(entry.get("removed_count") or len(removed)) - len(removed)
                text = ", ".join(removed[:8])
                if extra > 0:
                    text += f", +{extra} more"
                subparts.append(f"removed {text}")
            if subparts:
                parts.append(f"{label}: {'; '.join(subparts)}")
            continue

        parts.append(f"{label}: {entry.get('from', '-')} → {entry.get('to', '-')}")

    remaining = len(entries or []) - len(parts)
    if remaining > 0:
        parts.append(f"+{remaining} more change(s)")
    return "; ".join(parts)


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
        app_label = getattr(content_type, "app_label", "") if content_type else ""
        model_name = getattr(content_type, "model", "") if content_type else ""
        target_label = _model_label(app_label, model_name)
        target_repr = logentry.object_repr or ""
        change_message = logentry.get_change_message() or ""
        event_type = _event_type_from_action_flag(logentry.action_flag)
        action_label = build_admin_action_label(
            event_type=event_type,
            target_label=target_label,
            target_repr=target_repr,
            action_flag=logentry.action_flag,
            change_message=change_message,
            details={},
        )
        log_admin_activity(
            event_type=event_type,
            admin_user=logentry.user,
            admin_username=_safe_username(logentry.user),
            target_app_label=app_label,
            target_model=model_name,
            target_object_id=logentry.object_id or "",
            target_repr=target_repr,
            action_flag=logentry.action_flag,
            change_message=change_message,
            details={
                "source": "django_admin_logentry",
                "logentry_id": logentry.pk,
                "target_label": target_label,
                "target_display": target_repr,
                "action_label": action_label,
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

    path = request.path_info or request.path
    inferred = _admin_url_target_from_path(path)
    app_label = inferred.get("target_app_label", "")
    model_name = inferred.get("target_model", "")
    selected_names, selected_count = _selected_admin_objects(request, app_label, model_name)

    try:
        admin_action = request.POST.get("action", "")
    except Exception:
        admin_action = ""

    details = {
        "source": "admin_request_middleware",
        "query_string": request.META.get("QUERY_STRING", ""),
        "post_field_names": post_keys[:100],
        "post_field_count": len(post_keys),
        "content_type": request.META.get("CONTENT_TYPE", ""),
    }
    if admin_action:
        details["admin_action"] = admin_action
    if selected_count:
        details["selected_count"] = selected_count
        details["selected_objects_preview"] = selected_names[:10]

    return details


def infer_admin_request_context(request, response=None):
    """Return target fields and a human summary for a Django Admin request."""
    if request is None:
        return {}

    path = request.path_info or request.path
    inferred = _admin_url_target_from_path(path)
    details = request_post_metadata(request)

    app_label = inferred.get("target_app_label", "")
    model_name = inferred.get("target_model", "")
    target_label = _model_label(app_label, model_name)
    target_repr = inferred.get("target_repr", "")

    selected_preview = details.get("selected_objects_preview") or []
    selected_count = details.get("selected_count") or 0
    if not target_repr and selected_preview:
        shown = ", ".join(selected_preview[:5])
        extra = selected_count - len(selected_preview[:5])
        target_repr = f"{shown}, +{extra} more" if extra > 0 else shown

    status_code = getattr(response, "status_code", None)
    action_label = build_admin_action_label(
        event_type=None,
        target_label=target_label,
        target_repr=target_repr,
        status_code=status_code,
        details=details,
    )
    details["action_label"] = action_label
    if target_label:
        details["target_label"] = target_label
    if target_repr:
        details["target_display"] = target_repr

    return {
        "target_app_label": app_label,
        "target_model": model_name,
        "target_object_id": inferred.get("target_object_id", ""),
        "target_repr": target_repr,
        "change_message": action_label,
        "details": details,
    }
