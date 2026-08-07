"""Cleanup helpers for permanent user-account deletion.

Disabling a user or assigning the Disabled User role does not call these helpers.
They run only when the Django User row is actually deleted.
"""

from __future__ import annotations

import logging
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import Q

from .models import (
    ActivityLog,
    ArticleCreationWorkspace,
    ArticleEditWorkspace,
    ArticleImageUploadLog,
    SuggestedArticle,
)

logger = logging.getLogger(__name__)


def _enable_protected_audit_deletion() -> None:
    """Allow narrowly scoped deletion from append-only audit tables.

    PostgreSQL audit triggers permit deletion only when the transaction-local
    account-deletion cleanup flag is enabled. The flag is transaction-local
    and is used solely for rows that expose private article creation/edit checkpoints.
    """

    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL djopenkb.account_deletion_cleanup = 'on'")


def _checkpoint_activity_log_ids(
    *,
    user_id: int,
    creation_workspace_ids: set[str],
    edit_workspace_ids: set[str],
    filenames: set[str],
) -> list[int]:
    """Return image activity rows that belong only to deleted-account checkpoints."""

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
        creation_id = str(details.get("workspace_id") or "")
        edit_id = str(details.get("edit_workspace_id") or "")
        filename = str(details.get("filename") or "").strip()
        if (
            creation_id in creation_workspace_ids
            or edit_id in edit_workspace_ids
            or filename in filenames
        ):
            ids.append(log_entry.pk)
    return ids


def _purge_checkpoint_audit_rows(
    *,
    user_id: int,
    creation_workspace_ids: set,
    edit_workspace_ids: set,
    filenames: set[str],
) -> None:
    """Delete private checkpoint upload/activity rows inside user deletion."""

    upload_filter = Q()
    if creation_workspace_ids:
        upload_filter |= Q(creation_workspace_id__in=creation_workspace_ids)
    if edit_workspace_ids:
        upload_filter |= Q(edit_workspace_id__in=edit_workspace_ids)
    upload_log_ids = list(
        ArticleImageUploadLog.objects.filter(upload_filter).values_list("pk", flat=True)
    ) if upload_filter else []
    activity_log_ids = _checkpoint_activity_log_ids(
        user_id=user_id,
        creation_workspace_ids={str(value) for value in creation_workspace_ids},
        edit_workspace_ids={str(value) for value in edit_workspace_ids},
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


def _delete_private_account_files(filenames: set[str]) -> None:
    """Delete private/unpublished image files after account deletion commits.

    A defensive reference check prevents accidental removal if another saved
    article or another user's creation/edit workspace still references the same
    generated filename. This covers New Article checkpoints, personal edit
    drafts, unpublished articles removed with the account, and unpublished
    ``pending_update_*`` copies cleared from preserved published articles.
    """

    if not filenames:
        return

    from .views.services import (
        get_openkb_uploads_dir,
        image_is_used_by_other_article,
        image_is_used_by_other_creation_workspace,
        image_is_used_by_other_edit_workspace,
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
            if image_is_used_by_other_edit_workspace(filename):
                continue
        except Exception:
            logger.exception(
                "Unable to verify references before purging deleted-account private image %s",
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
                "Unable to purge private article image after account deletion: %s",
                filename,
            )


def _delete_unpublished_article_markdown(articles: list[SuggestedArticle]) -> None:
    """Remove generated Markdown for unpublished articles deleted with an account."""

    if not articles:
        return

    from .views.services import delete_article_markdown_files

    for article in articles:
        try:
            delete_article_markdown_files(article)
        except OSError:
            logger.exception(
                "Unable to purge unpublished article Markdown after account deletion: article_id=%s",
                article.pk,
            )


def _article_private_image_filenames(article: SuggestedArticle) -> set[str]:
    """Return uploaded image filenames held by live or staged content on ``article``."""

    from .views.services import extract_article_image_filenames, safe_uploaded_filename

    candidates = set(article.image_assets or [])
    candidates.update(extract_article_image_filenames(article.body or ""))
    candidates.update(article.pending_update_image_assets or [])
    candidates.update(extract_article_image_filenames(article.pending_update_body or ""))
    snapshot = article.review_submission_snapshot or {}
    if isinstance(snapshot, dict):
        candidates.update(snapshot.get("image_assets") or [])
        candidates.update(extract_article_image_filenames(snapshot.get("body") or ""))
    return {
        safe_name
        for filename in candidates
        if (safe_name := safe_uploaded_filename(filename))
    }


def prepare_user_account_deletion(user) -> None:
    """Prepare privacy cleanup before Django permanently deletes a User row.

    Disabled/inactive accounts are untouched because this helper runs only for
    real User deletion. Unpublished Draft/Pending/Pending-failed articles are
    deleted with the account. Published (and deletion-queued published) knowledge
    is preserved as orphaned content with author snapshots, but any unpublished
    ``pending_update_*`` copy owned by the deleted account is cleared.
    """

    user_id = getattr(user, "pk", None)
    if not user_id:
        return

    creation_workspaces = list(
        ArticleCreationWorkspace.objects.select_for_update().filter(owner_id=user_id)
    )
    edit_workspaces = list(
        ArticleEditWorkspace.objects.select_for_update().filter(owner_id=user_id)
    )
    creation_workspace_ids = {workspace.pk for workspace in creation_workspaces}
    edit_workspace_ids = {workspace.pk for workspace in edit_workspaces}

    # Saved but unpublished article rows are private user content, unlike an
    # already-published article that must remain useful after staff turnover.
    private_statuses = {
        SuggestedArticle.Status.DRAFT,
        SuggestedArticle.Status.PENDING,
        SuggestedArticle.Status.FAILED,
    }
    owned_articles = list(
        SuggestedArticle.objects.select_for_update()
        .filter(owner_id=user_id)
        .order_by("pk")
    )
    unpublished_articles = [
        article for article in owned_articles if article.status in private_statuses
    ]
    preserved_articles = [
        article for article in owned_articles if article.status not in private_statuses
    ]

    from .views.services import extract_article_image_filenames, safe_uploaded_filename

    filenames: set[str] = set()
    for article in unpublished_articles:
        filenames.update(_article_private_image_filenames(article))
    for article in preserved_articles:
        # Only the unpublished staged copy is private. The published article's
        # committed image assets remain with the orphaned published knowledge.
        staged_candidates = set(article.pending_update_image_assets or [])
        staged_candidates.update(extract_article_image_filenames(article.pending_update_body or ""))
        snapshot = article.review_submission_snapshot or {}
        if isinstance(snapshot, dict):
            staged_candidates.update(snapshot.get("image_assets") or [])
            staged_candidates.update(extract_article_image_filenames(snapshot.get("body") or ""))
        filenames.update(
            safe_name
            for filename in staged_candidates
            if (safe_name := safe_uploaded_filename(filename))
        )

    if creation_workspaces or edit_workspaces:
        from .views.services import (
            article_creation_workspace_assets,
            article_edit_workspace_owned_assets,
            safe_uploaded_filename,
        )

        checkpoint_filenames: set[str] = set()
        for workspace in creation_workspaces:
            checkpoint_filenames.update(article_creation_workspace_assets(workspace))
        for workspace in edit_workspaces:
            checkpoint_filenames.update(article_edit_workspace_owned_assets(workspace))
        checkpoint_filenames.update(
            ArticleImageUploadLog.objects.filter(
                Q(creation_workspace_id__in=creation_workspace_ids)
                | Q(edit_workspace_id__in=edit_workspace_ids)
            ).values_list("filename", flat=True)
        )
        checkpoint_filenames = {
            safe_name
            for filename in checkpoint_filenames
            if (safe_name := safe_uploaded_filename(filename))
        }
        filenames.update(checkpoint_filenames)
        _purge_checkpoint_audit_rows(
            user_id=user_id,
            creation_workspace_ids=creation_workspace_ids,
            edit_workspace_ids=edit_workspace_ids,
            filenames=checkpoint_filenames,
        )

    # Remove unpublished saved articles inside the same database transaction as
    # account deletion. Their generated files are removed only after commit.
    unpublished_article_ids = [article.pk for article in unpublished_articles]
    if unpublished_article_ids:
        SuggestedArticle.objects.filter(pk__in=unpublished_article_ids).delete()

    preserved_article_ids = [article.pk for article in preserved_articles]
    if preserved_article_ids:
        # Published knowledge survives as an orphan, but private update drafts,
        # submitted updates, rejection state, and staged images do not survive
        # permanent deletion of their owner.
        SuggestedArticle.objects.filter(pk__in=preserved_article_ids).update(
            pending_update_title="",
            pending_update_body="",
            pending_update_keywords="",
            pending_update_image_assets=[],
            update_status=SuggestedArticle.UpdateStatus.NONE,
            update_submitted_at=None,
            update_reviewed_at=None,
            review_notes="",
            review_submission_snapshot={},
        )

    # These callbacks run only after the account deletion transaction commits.
    transaction.on_commit(lambda uid=user_id: _delete_user_sessions(uid))
    transaction.on_commit(lambda owned_files=set(filenames): _delete_private_account_files(owned_files))
    transaction.on_commit(
        lambda articles=list(unpublished_articles): _delete_unpublished_article_markdown(articles)
    )

    try:
        from .auth_monitoring import reset_auth_lockout

        def clear_lockouts(uid=user_id):
            for purpose in ("password", "mfa", "admin_mfa"):
                reset_auth_lockout(f"{purpose}:user:{uid}")

        transaction.on_commit(clear_lockouts)
    except Exception:
        pass
