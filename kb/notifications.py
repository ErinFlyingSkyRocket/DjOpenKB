"""Direct, privacy-preserving SMTP helpers for article workflow events.

Notifications are sent after the article transaction commits. Submission events
resolve the current Django reviewer role groups and use one Bcc-only message so
reviewer membership is not exposed. Final review outcomes resolve only the
current eligible article owner and use one direct To message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.urls import reverse

from .models import ActivityLog, SuggestedArticle
from .permissions import (
    ROLE_ADMIN_USERS,
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_DISABLED_USER,
    ROLE_INTERNAL_ARTICLE_APPROVER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
)


logger = logging.getLogger(__name__)

NOTIFICATION_KIND_NEW_SUBMISSION = "new_submission"
NOTIFICATION_KIND_UPDATE_SUBMISSION = "update_submission"
_VALID_NOTIFICATION_KINDS = {
    NOTIFICATION_KIND_NEW_SUBMISSION,
    NOTIFICATION_KIND_UPDATE_SUBMISSION,
}

# Owner decision notifications are deliberately separate from reviewer-pool
# notifications. A reviewer submission notifies a role-scoped Bcc pool, while
# an approval/rejection result notifies only the article owner in the To field.
OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED = "article_approved"
OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED = "article_pending_failed"
OWNER_NOTIFICATION_KIND_UPDATE_APPROVED = "update_approved"
OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED = "update_pending_failed"
_VALID_OWNER_NOTIFICATION_KINDS = {
    OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED,
    OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED,
    OWNER_NOTIFICATION_KIND_UPDATE_APPROVED,
    OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED,
}


@dataclass(frozen=True)
class ReviewRecipient:
    """Minimal recipient data required for the Bcc envelope."""

    user_id: int
    email: str


@dataclass(frozen=True)
class ArticleOwnerRecipient:
    """Validated active owner address for one outcome notification."""

    user_id: int
    email: str


def article_review_notifications_enabled() -> bool:
    return bool(getattr(settings, "EMAIL_NOTIFICATIONS_ENABLED", False))


def _reviewer_group_names(article: SuggestedArticle) -> tuple[str, ...]:
    """Return the exact reviewer groups for the article visibility scope."""
    if article.is_internal:
        return (
            ROLE_INTERNAL_ARTICLE_APPROVER,
            ROLE_INTERNAL_ARTICLE_MANAGER,
            ROLE_ADMIN_USERS,
        )
    return (
        ROLE_ARTICLE_APPROVER,
        ROLE_ARTICLE_MANAGER,
        ROLE_ADMIN_USERS,
    )


def _allowed_recipient_domains() -> set[str]:
    """Normalise the explicit SMTP-recipient domain allowlist from settings."""
    configured = getattr(settings, "SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS", ())
    if isinstance(configured, str):
        configured = configured.split(",")
    return {
        str(domain).strip().casefold()
        for domain in configured
        if str(domain).strip()
    }


def get_article_review_recipients(article: SuggestedArticle) -> list[ReviewRecipient]:
    """Return active, enabled reviewers with an allowed organisation email.

    The current application role groups are the only source of truth. A Django
    superuser receives review notifications only when they are also assigned to
    the Admin Users group, preventing stale direct-superuser flags from widening
    the recipient pool. The query returns only IDs and email values, avoiding a
    per-recipient AD or application-profile lookup during a submission.
    """
    User = get_user_model()
    reviewer_roles = _reviewer_group_names(article)
    allowed_domains = _allowed_recipient_domains()

    candidates = (
        User.objects.filter(is_active=True, groups__name__in=reviewer_roles)
        .exclude(groups__name=ROLE_DISABLED_USER)
        .exclude(kb_profile__can_access_main_site=False)
        .exclude(email="")
        .distinct()
        .values_list("id", "email")
    )

    recipients: list[ReviewRecipient] = []
    seen_emails: set[str] = set()

    for user_id, raw_email in candidates:
        email = (raw_email or "").strip()
        normalised_email = email.casefold()
        if not email or normalised_email in seen_emails:
            continue
        try:
            validate_email(email)
        except ValidationError:
            logger.warning(
                "Skipping article review notification recipient with invalid email: user_id=%s",
                user_id,
            )
            continue

        email_domain = email.rpartition("@")[2].casefold()
        if not email_domain or email_domain not in allowed_domains:
            logger.warning(
                "Skipping article review notification recipient outside the configured domain allowlist: user_id=%s",
                user_id,
            )
            continue

        seen_emails.add(normalised_email)
        recipients.append(ReviewRecipient(user_id=user_id, email=email))

    return recipients


def _article_is_still_awaiting_review(article: SuggestedArticle, notification_kind: str) -> bool:
    if notification_kind == NOTIFICATION_KIND_NEW_SUBMISSION:
        return article.status == SuggestedArticle.Status.PENDING
    if notification_kind == NOTIFICATION_KIND_UPDATE_SUBMISSION:
        return bool(
            article.status == SuggestedArticle.Status.PUBLISHED
            and article.update_status == SuggestedArticle.UpdateStatus.PENDING
        )
    return False


def _review_url(article: SuggestedArticle) -> str:
    route_name = (
        "manage_internal_pending_articles"
        if article.is_internal
        else "manage_pending_articles"
    )
    return f"{settings.SITE_BASE_URL}{reverse(route_name)}"


def _clean_public_article_title(article: SuggestedArticle) -> str:
    return " ".join((article.title or "").split())[:200] or "(untitled article)"


def _build_message(article: SuggestedArticle, notification_kind: str) -> tuple[str, str]:
    """Return a deliberately minimal subject/body without article content."""
    is_update = notification_kind == NOTIFICATION_KIND_UPDATE_SUBMISSION
    scope = "internal" if article.is_internal else "public"
    item = "article update" if is_update else "article"

    subject = f"{settings.EMAIL_SUBJECT_PREFIX}{scope.title()} {item} review required"

    lines = [
        f"A {scope} {item} is awaiting review in Knowledge Repository.",
    ]
    if article.is_internal:
        # Internal titles/content stay out of inboxes and relay logs.
        lines.append(
            "The title and content are intentionally omitted because this is an internal submission."
        )
    else:
        lines.append(f"Article title: {_clean_public_article_title(article)}")

    lines.extend(
        [
            "",
            "Sign in and open the pending-review page:",
            _review_url(article),
            "",
            "This is an automated Knowledge Repository notification.",
        ]
    )
    return subject, "\n".join(lines)


def _safe_activity_log(
    event_type: str,
    *,
    article: SuggestedArticle,
    user_id: int | None,
    details: dict,
) -> None:
    """Record counts/statuses only; never record addresses or SMTP secrets."""
    try:
        from .views.services import log_activity

        actor = None
        if user_id:
            User = get_user_model()
            actor = User.objects.filter(pk=user_id).first()

        log_activity(
            None,
            event_type,
            article=article,
            user=actor,
            details=details,
        )
    except Exception:
        logger.exception(
            "Unable to record article notification activity: event_type=%s article_id=%s",
            event_type,
            getattr(article, "pk", None),
        )


def _send_bcc_message(
    *,
    subject: str,
    body: str,
    recipient_emails: list[str],
) -> bool:
    """Send one Bcc-only SMTP message using a single relay connection.

    Django supplies the Bcc values as SMTP envelope recipients but does not put
    them in the message headers. The relay can report acceptance of the message,
    not final mailbox delivery, so callers record this as relay acceptance.
    """
    if not recipient_emails:
        return False

    connection = get_connection(fail_silently=False)
    try:
        opened = connection.open()
        if opened is False and getattr(connection, "connection", None) is None:
            raise RuntimeError("SMTP backend did not open a relay connection.")

        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[],
            bcc=recipient_emails,
            connection=connection,
        )
        return bool(message.send(fail_silently=False))
    finally:
        try:
            connection.close()
        except Exception:
            logger.warning("Could not close SMTP relay connection cleanly.")


def deliver_article_review_notification(
    article_id: int,
    notification_kind: str,
    submitted_by_user_id: int | None = None,
) -> dict:
    """Resolve recipients and send one privacy-preserving Bcc notification."""
    if not article_review_notifications_enabled():
        return {"status": "disabled"}

    if notification_kind not in _VALID_NOTIFICATION_KINDS:
        logger.error(
            "Ignoring article review notification with unsupported kind: article_id=%s kind=%s",
            article_id,
            notification_kind,
        )
        return {"status": "invalid_kind"}

    article = SuggestedArticle.objects.select_related("owner").filter(pk=article_id).first()
    if article is None:
        return {"status": "article_missing"}

    if not _article_is_still_awaiting_review(article, notification_kind):
        return {"status": "no_longer_pending"}

    recipients = get_article_review_recipients(article)
    if not recipients:
        _safe_activity_log(
            ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_SKIPPED,
            article=article,
            user_id=submitted_by_user_id,
            details={
                "notification_kind": notification_kind,
                "visibility": article.visibility,
                "reason": "no_eligible_recipients",
            },
        )
        logger.warning(
            "No eligible article-review notification recipients: article_id=%s kind=%s",
            article.pk,
            notification_kind,
        )
        return {"status": "no_recipients"}

    subject, body = _build_message(article, notification_kind)
    recipient_emails = [recipient.email for recipient in recipients]

    try:
        accepted = _send_bcc_message(
            subject=subject,
            body=body,
            recipient_emails=recipient_emails,
        )
    except Exception as exc:
        logger.error(
            "Article review notification relay submission failed: article_id=%s error=%s",
            article.pk,
            type(exc).__name__,
        )
        _safe_activity_log(
            ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_FAILED,
            article=article,
            user_id=submitted_by_user_id,
            details={
                "notification_kind": notification_kind,
                "visibility": article.visibility,
                "recipient_count": len(recipients),
                "relay_accepted_count": 0,
                "transport": "single_bcc_message",
                "reason": "smtp_send_failed",
                "error_type": type(exc).__name__,
            },
        )
        return {
            "status": "failed",
            "recipient_count": len(recipients),
            "relay_accepted_count": 0,
        }

    relay_accepted_count = len(recipients) if accepted else 0
    event_type = (
        ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_SENT
        if accepted
        else ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_FAILED
    )
    _safe_activity_log(
        event_type,
        article=article,
        user_id=submitted_by_user_id,
        details={
            "notification_kind": notification_kind,
            "visibility": article.visibility,
            "recipient_count": len(recipients),
            "relay_accepted_count": relay_accepted_count,
            "transport": "single_bcc_message",
            "reason": "relay_did_not_accept_message" if not accepted else "",
        },
    )

    return {
        "status": "sent" if accepted else "failed",
        "recipient_count": len(recipients),
        "relay_accepted_count": relay_accepted_count,
    }


def send_article_review_notification_after_commit(
    request,
    article: SuggestedArticle,
    notification_kind: str,
) -> bool:
    """Send a review notification only after the article transaction commits.

    Direct SMTP keeps the deployment lightweight: no notification Celery queue or
    worker is required. A relay failure is caught and audited; it never reverses
    a valid pending submission or turns the author response into a 500 error.
    """
    if not article_review_notifications_enabled():
        return False
    if notification_kind not in _VALID_NOTIFICATION_KINDS:
        raise ValueError("Unsupported article review notification kind.")
    if not _article_is_still_awaiting_review(article, notification_kind):
        return False

    article_id = article.pk
    actor_id = (
        request.user.pk
        if request is not None
        and getattr(request, "user", None) is not None
        and getattr(request.user, "is_authenticated", False)
        else None
    )

    def _send_after_commit() -> None:
        try:
            deliver_article_review_notification(
                article_id,
                notification_kind,
                actor_id,
            )
        except Exception as exc:
            # This is a final safety net for unexpected programming/database
            # errors. The email path must never undo or mask a valid submission.
            logger.exception(
                "Unexpected article review notification failure: article_id=%s kind=%s",
                article_id,
                notification_kind,
            )
            committed_article = SuggestedArticle.objects.filter(pk=article_id).first()
            if committed_article is not None:
                _safe_activity_log(
                    ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_FAILED,
                    article=committed_article,
                    user_id=actor_id,
                    details={
                        "notification_kind": notification_kind,
                        "visibility": committed_article.visibility,
                        "reason": "unexpected_notification_error",
                        "error_type": type(exc).__name__,
                    },
                )

    transaction.on_commit(_send_after_commit)
    return True


# ---------------------------------------------------------------------------
# Individual article-owner decision notifications
# ---------------------------------------------------------------------------


def _get_article_owner_notification_recipient(
    article: SuggestedArticle,
) -> tuple[ArticleOwnerRecipient | None, str]:
    """Resolve the current eligible article owner without trusting snapshots.

    The database owner relation is required because an owner notification includes
    a sign-in link. Historical author snapshots are intentionally never used as
    an email fallback after an account is deleted, disabled, or blocked.
    """
    owner = article.owner
    if owner is None:
        return None, "article_owner_missing"
    if not owner.is_active:
        return None, "article_owner_inactive"

    profile = getattr(owner, "kb_profile", None)
    if profile is not None and not profile.can_access_main_site:
        return None, "article_owner_main_site_blocked"

    if owner.groups.filter(name=ROLE_DISABLED_USER).exists():
        return None, "article_owner_disabled"

    email = (owner.email or "").strip()
    if not email:
        return None, "article_owner_email_missing"

    try:
        validate_email(email)
    except ValidationError:
        logger.warning(
            "Skipping article owner notification with invalid email: article_id=%s owner_id=%s",
            article.pk,
            owner.pk,
        )
        return None, "article_owner_email_invalid"

    email_domain = email.rpartition("@")[2].casefold()
    if not email_domain or email_domain not in _allowed_recipient_domains():
        logger.warning(
            "Skipping article owner notification outside the configured domain allowlist: "
            "article_id=%s owner_id=%s",
            article.pk,
            owner.pk,
        )
        return None, "article_owner_email_domain_not_allowed"

    return ArticleOwnerRecipient(user_id=owner.pk, email=email), ""


def _owner_notification_matches_article_state(
    article: SuggestedArticle,
    notification_kind: str,
) -> bool:
    """Ensure a post-commit outcome still reflects the current workflow state."""
    if notification_kind == OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED:
        return article.status == SuggestedArticle.Status.PUBLISHED
    if notification_kind == OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED:
        return article.status == SuggestedArticle.Status.FAILED
    if notification_kind == OWNER_NOTIFICATION_KIND_UPDATE_APPROVED:
        return bool(
            article.status == SuggestedArticle.Status.PUBLISHED
            and article.update_status == SuggestedArticle.UpdateStatus.NONE
            and article.update_reviewed_at is not None
        )
    if notification_kind == OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED:
        return bool(
            article.status == SuggestedArticle.Status.PUBLISHED
            and article.update_status == SuggestedArticle.UpdateStatus.FAILED
        )
    return False


def _owner_notification_url(article: SuggestedArticle, notification_kind: str) -> str:
    """Return an authenticated destination appropriate for the outcome."""
    if notification_kind in {
        OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED,
        OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED,
    }:
        path = reverse("edit_suggestion", kwargs={"article_id": article.pk})
    else:
        path = article.public_url
    return f"{settings.SITE_BASE_URL}{path}"


def _owner_notification_subject_and_body(
    article: SuggestedArticle,
    notification_kind: str,
) -> tuple[str, str]:
    """Build an outcome email without article content or review comments.

    Review comments may be sensitive operational information, and including
    them in mailboxes or relay logs would widen their storage boundary. The
    author receives a direct authenticated link to read the current comments in
    DjOpenKB instead. Internal article titles are likewise intentionally omitted.
    """
    scope = "internal" if article.is_internal else "public"
    if notification_kind == OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED:
        subject = f"{settings.EMAIL_SUBJECT_PREFIX}Your {scope} article is approved"
        outcome_lines = [
            f"Your {scope} article has been approved and is now published.",
            "You do not need to take any further action.",
        ]
    elif notification_kind == OWNER_NOTIFICATION_KIND_UPDATE_APPROVED:
        subject = f"{settings.EMAIL_SUBJECT_PREFIX}Your {scope} article update is approved"
        outcome_lines = [
            f"Your submitted update to a {scope} article has been approved and is now published.",
            "You do not need to take any further action.",
        ]
    elif notification_kind == OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED:
        subject = f"{settings.EMAIL_SUBJECT_PREFIX}Your {scope} article needs changes"
        outcome_lines = [
            f"Your {scope} article was marked as Pending failed and has not been published.",
            "Review the reviewer comments, update the article, and resubmit it for approval.",
        ]
    elif notification_kind == OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED:
        subject = f"{settings.EMAIL_SUBJECT_PREFIX}Your {scope} article update needs changes"
        outcome_lines = [
            f"Your submitted update to a {scope} article was marked as Pending failed.",
            "The current published version remains visible. Review the reviewer comments, update the draft, and resubmit it for approval.",
        ]
    else:
        raise ValueError("Unsupported article owner notification kind.")

    lines = outcome_lines[:]
    if article.is_internal:
        lines.append(
            "The internal article title, content, and review comments are intentionally not included in this email."
        )
    else:
        lines.append(f"Article title: {_clean_public_article_title(article)}")

    lines.extend(
        [
            "",
            "Sign in to Knowledge Repository:",
            _owner_notification_url(article, notification_kind),
            "",
            "This is an automated Knowledge Repository notification.",
        ]
    )
    return subject, "\n".join(lines)


def _send_individual_message(*, subject: str, body: str, recipient_email: str) -> bool:
    """Send one outcome message to one validated owner address."""
    connection = get_connection(fail_silently=False)
    try:
        opened = connection.open()
        if opened is False and getattr(connection, "connection", None) is None:
            raise RuntimeError("SMTP backend did not open a relay connection.")

        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient_email],
            connection=connection,
        )
        return bool(message.send(fail_silently=False))
    finally:
        try:
            connection.close()
        except Exception:
            logger.warning("Could not close SMTP relay connection cleanly.")


def deliver_article_owner_notification(
    article_id: int,
    notification_kind: str,
    reviewed_by_user_id: int | None = None,
) -> dict:
    """Send a single privacy-preserving decision notification to the owner."""
    if not article_review_notifications_enabled():
        return {"status": "disabled"}

    if notification_kind not in _VALID_OWNER_NOTIFICATION_KINDS:
        logger.error(
            "Ignoring article owner notification with unsupported kind: article_id=%s kind=%s",
            article_id,
            notification_kind,
        )
        return {"status": "invalid_kind"}

    article = (
        SuggestedArticle.objects.select_related("owner", "owner__kb_profile")
        .filter(pk=article_id)
        .first()
    )
    if article is None:
        return {"status": "article_missing"}

    if not _owner_notification_matches_article_state(article, notification_kind):
        return {"status": "outcome_no_longer_current"}

    recipient, skip_reason = _get_article_owner_notification_recipient(article)
    if recipient is None:
        _safe_activity_log(
            ActivityLog.EventType.ARTICLE_OWNER_NOTIFICATION_SKIPPED,
            article=article,
            user_id=reviewed_by_user_id,
            details={
                "notification_kind": notification_kind,
                "visibility": article.visibility,
                "reason": skip_reason,
            },
        )
        return {"status": "no_recipient", "reason": skip_reason}

    if reviewed_by_user_id and recipient.user_id == reviewed_by_user_id:
        _safe_activity_log(
            ActivityLog.EventType.ARTICLE_OWNER_NOTIFICATION_SKIPPED,
            article=article,
            user_id=reviewed_by_user_id,
            details={
                "notification_kind": notification_kind,
                "visibility": article.visibility,
                "reason": "reviewer_is_article_owner",
            },
        )
        return {"status": "self_review", "reason": "reviewer_is_article_owner"}

    subject, body = _owner_notification_subject_and_body(article, notification_kind)
    try:
        accepted = _send_individual_message(
            subject=subject,
            body=body,
            recipient_email=recipient.email,
        )
    except Exception as exc:
        logger.error(
            "Article owner notification relay submission failed: article_id=%s error=%s",
            article.pk,
            type(exc).__name__,
        )
        _safe_activity_log(
            ActivityLog.EventType.ARTICLE_OWNER_NOTIFICATION_FAILED,
            article=article,
            user_id=reviewed_by_user_id,
            details={
                "notification_kind": notification_kind,
                "visibility": article.visibility,
                "recipient_count": 1,
                "relay_accepted_count": 0,
                "transport": "single_to_message",
                "reason": "smtp_send_failed",
                "error_type": type(exc).__name__,
            },
        )
        return {"status": "failed", "recipient_count": 1, "relay_accepted_count": 0}

    relay_accepted_count = 1 if accepted else 0
    _safe_activity_log(
        (
            ActivityLog.EventType.ARTICLE_OWNER_NOTIFICATION_SENT
            if accepted
            else ActivityLog.EventType.ARTICLE_OWNER_NOTIFICATION_FAILED
        ),
        article=article,
        user_id=reviewed_by_user_id,
        details={
            "notification_kind": notification_kind,
            "visibility": article.visibility,
            "recipient_count": 1,
            "relay_accepted_count": relay_accepted_count,
            "transport": "single_to_message",
            "reason": "relay_did_not_accept_message" if not accepted else "",
        },
    )
    return {
        "status": "sent" if accepted else "failed",
        "recipient_count": 1,
        "relay_accepted_count": relay_accepted_count,
    }


def send_article_owner_notification_after_commit(
    request,
    article: SuggestedArticle,
    notification_kind: str,
) -> bool:
    """Schedule one owner decision email after the review transaction commits.

    Email delivery is best-effort by design. A relay failure is audited but never
    reverses a valid approval/rejection decision or turns the reviewer response
    into a server error.
    """
    if not article_review_notifications_enabled():
        return False
    if notification_kind not in _VALID_OWNER_NOTIFICATION_KINDS:
        raise ValueError("Unsupported article owner notification kind.")
    if not _owner_notification_matches_article_state(article, notification_kind):
        return False

    article_id = article.pk
    actor_id = (
        request.user.pk
        if request is not None
        and getattr(request, "user", None) is not None
        and getattr(request.user, "is_authenticated", False)
        else None
    )

    _safe_activity_log(
        ActivityLog.EventType.ARTICLE_OWNER_NOTIFICATION_QUEUED,
        article=article,
        user_id=actor_id,
        details={
            "notification_kind": notification_kind,
            "visibility": article.visibility,
            "transport": "single_to_message",
        },
    )

    def _send_after_commit() -> None:
        try:
            deliver_article_owner_notification(article_id, notification_kind, actor_id)
        except Exception as exc:
            logger.exception(
                "Unexpected article owner notification failure: article_id=%s kind=%s",
                article_id,
                notification_kind,
            )
            committed_article = SuggestedArticle.objects.filter(pk=article_id).first()
            if committed_article is not None:
                _safe_activity_log(
                    ActivityLog.EventType.ARTICLE_OWNER_NOTIFICATION_FAILED,
                    article=committed_article,
                    user_id=actor_id,
                    details={
                        "notification_kind": notification_kind,
                        "visibility": committed_article.visibility,
                        "reason": "unexpected_notification_error",
                        "error_type": type(exc).__name__,
                    },
                )

    transaction.on_commit(_send_after_commit)
    return True
