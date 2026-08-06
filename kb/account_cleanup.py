"""Cleanup helpers for permanent user-account deletion.

Disabling a user or assigning the Disabled User role does not call these helpers.
They run only when the Django User row is actually deleted.
"""

from __future__ import annotations

import logging
from django.contrib.sessions.models import Session
from django.db import connection, transaction

from .models import ActivityLog, ArticleCreationWorkspace, ArticleImageUploadLog

logger = logging.getLogger(__name__)


def _enable_protected_audit_deletion() -> None:
    """Allow narrowly scoped deletion from append-only audit tables.

    PostgreSQL audit triggers permit deletion only when the transaction-local
    account-deletion cleanup flag is enabled. The flag is transaction-local
    and is used solely for rows that expose the active New Article checkpoint.
    """

    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL djopenkb.account_deletion_cleanup = 'on'")


def _checkpoint_activity_log_ids(*, user_id: int, workspace_id: str, filenames: set[str]) -> list[int]:
    """Return image activity rows that belong only to one active checkpoint."""

    ids: list[int] = []
    queryset = ActivityLog.objects.filter(
        user_id=user_id,
        event_type__in=(
            ActivityLog.EventType.IMAGE_UPLOADED,
            ActivityLog.EventType.IMAGE_DELETED,
        ),
    ).only("pk", "details")

    for log_entry in queryset.iterator():
        details = log_entry.details if isinstance(log_entry.details, dict) else {}
        details_workspace_id = str(details.get("workspace_id") or "")
        details_filename = str(details.get("filename") or "").strip()
        if details_workspace_id == workspace_id or details_filename in filenames:
            ids.append(log_entry.pk)
    return ids


def _purge_checkpoint_audit_rows(*, user_id: int, workspace_id, filenames: set[str]) -> None:
    """Delete checkpoint-only upload/activity rows inside the user-delete transaction."""

    upload_log_ids = list(
        ArticleImageUploadLog.objects.filter(
            creation_workspace_id=workspace_id,
        ).values_list("pk", flat=True)
    )
    activity_log_ids = _checkpoint_activity_log_ids(
        user_id=user_id,
        workspace_id=str(workspace_id),
        filenames=filenames,
    )

    if not upload_log_ids and not activity_log_ids:
        return

    _enable_protected_audit_deletion()
    if upload_log_ids:
        ArticleImageUploadLog.objects.filter(pk__in=upload_log_ids).delete()
    if activity_log_ids:
        ActivityLog.objects.filter(pk__in=activity_log_ids).delete()


def _delete_user_sessions(user_id: int) -> None:
    """Remove authenticated and pending-MFA sessions for a permanently deleted user."""

    user_id_text = str(user_id)
    try:
        from .mfa import MFA_USER_SESSION_KEY, PRE_MFA_USER_ID_SESSION_KEY
    except Exception:
        MFA_USER_SESSION_KEY = "djopenkb_mfa_verified_user_id"
        PRE_MFA_USER_ID_SESSION_KEY = "djopenkb_pre_mfa_user_id"

    session_keys: list[str] = []
    for session in Session.objects.all().iterator():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if (
            str(data.get("_auth_user_id") or "") == user_id_text
            or str(data.get(PRE_MFA_USER_ID_SESSION_KEY) or "") == user_id_text
            or str(data.get(MFA_USER_SESSION_KEY) or "") == user_id_text
        ):
            session_keys.append(session.session_key)

    if session_keys:
        Session.objects.filter(session_key__in=session_keys).delete()


def _delete_checkpoint_files(filenames: set[str]) -> None:
    """Delete uncommitted checkpoint files after the account deletion commits.

    A defensive reference check prevents accidental removal if inconsistent
    legacy data points a published article or another checkpoint at the same
    generated filename.
    """

    if not filenames:
        return

    from .views.services import (
        get_openkb_uploads_dir,
        image_is_used_by_other_article,
        image_is_used_by_other_creation_workspace,
        safe_uploaded_filename,
    )

    upload_dir = get_openkb_uploads_dir().resolve()
    for raw_filename in filenames:
        filename = safe_uploaded_filename(raw_filename)
        if not filename:
            continue
        try:
            if image_is_used_by_other_article(filename):
                continue
            if image_is_used_by_other_creation_workspace(filename):
                continue
        except Exception:
            logger.exception(
                "Unable to verify references before purging deleted-account checkpoint image %s",
                filename,
            )
            continue

        file_path = (upload_dir / filename).resolve()
        try:
            file_path.relative_to(upload_dir)
        except ValueError:
            continue

        try:
            if file_path.is_file():
                file_path.unlink()
        except OSError:
            # The normal stray-file cleanup remains the recovery path if a
            # filesystem failure prevents immediate deletion.
            logger.exception(
                "Unable to purge New Article checkpoint image after account deletion: %s",
                filename,
            )


def prepare_user_account_deletion(user) -> None:
    """Prepare permanent account cleanup before Django deletes the User row.

    Existing SuggestedArticle rows are intentionally untouched; their owner
    foreign key uses SET_NULL and their author snapshots remain available.
    """

    user_id = getattr(user, "pk", None)
    if not user_id:
        return

    workspace = (
        ArticleCreationWorkspace.objects.select_for_update()
        .filter(owner_id=user_id)
        .first()
    )

    filenames: set[str] = set()
    if workspace is not None:
        from .views.services import article_creation_workspace_assets, safe_uploaded_filename

        filenames.update(article_creation_workspace_assets(workspace))
        filenames.update(
            ArticleImageUploadLog.objects.filter(
                creation_workspace_id=workspace.pk,
            ).values_list("filename", flat=True)
        )
        filenames = {
            safe_name
            for filename in filenames
            if (safe_name := safe_uploaded_filename(filename))
        }
        _purge_checkpoint_audit_rows(
            user_id=user_id,
            workspace_id=workspace.pk,
            filenames=filenames,
        )

    # These callbacks run only after the account deletion transaction commits.
    transaction.on_commit(lambda uid=user_id: _delete_user_sessions(uid))
    transaction.on_commit(lambda owned_files=set(filenames): _delete_checkpoint_files(owned_files))

    try:
        from .auth_monitoring import reset_auth_lockout

        def clear_lockouts(uid=user_id):
            for purpose in ("password", "mfa", "admin_mfa"):
                reset_auth_lockout(f"{purpose}:user:{uid}")

        transaction.on_commit(clear_lockouts)
    except Exception:
        pass
