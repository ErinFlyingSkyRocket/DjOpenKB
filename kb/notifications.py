"""Asynchronous, role-scoped article review notification helpers.

This module never trusts a user-supplied recipient address. Recipients are
resolved from the project's Django role groups at delivery time and each person
receives a separate message so reviewer membership is not disclosed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
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


class SMTPRelayConnectionError(RuntimeError):
    """Raised only when no SMTP connection was opened and retry is safe."""


@dataclass(frozen=True)
class ReviewRecipient:
    """Minimal recipient data required to send an individual email."""

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
    """Return active, enabled reviewer accounts with a valid email address.

    Admin Users are included through their role group and direct superusers are
    included defensively in case an older account has not yet been normalised
    into the Admin Users group. Disabled or main-site-blocked accounts are
    excluded even if a stale role or superuser flag remains. The configured
    recipient-domain allowlist prevents a role holder with an unexpected
    external address from receiving review links.
    """
    User = get_user_model()
    reviewer_roles = _reviewer_group_names(article)
    allowed_domains = _allowed_recipient_domains()

    candidates = (
        User.objects.filter(is_active=True)
        .filter(Q(groups__name__in=reviewer_roles) | Q(is_superuser=True))
        .exclude(groups__name=ROLE_DISABLED_USER)
        .exclude(kb_profile__can_access_main_site=False)
        .exclude(email="")
        .distinct()
        .only("id", "email")
    )

    recipients: list[ReviewRecipient] = []
    seen_emails: set[str] = set()

    for user in candidates:
        email = (user.email or "").strip()
        normalised_email = email.casefold()
        if not email or normalised_email in seen_emails:
            continue
        try:
            validate_email(email)
        except ValidationError:
            logger.warning(
                "Skipping article review notification recipient with invalid email: user_id=%s",
                user.pk,
            )
            continue

        email_domain = email.rpartition("@")[2].casefold()
        if not email_domain or email_domain not in allowed_domains:
            logger.warning(
                "Skipping article review notification recipient outside the configured domain allowlist: user_id=%s",
                user.pk,
            )
            continue

        seen_emails.add(normalised_email)
        recipients.append(ReviewRecipient(user_id=user.pk, email=email))

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
        # Internal article titles/content stay out of email inboxes and relay logs.
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
    """Record only counts/statuses, never recipient email addresses."""
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


def enqueue_article_review_notification(
    request,
    article: SuggestedArticle,
    notification_kind: str,
) -> bool:
    """Queue a notification after the submission's database transaction commits.

    Failure to publish a Celery task does not undo a valid article submission.
    The problem is logged/audited so administrators can diagnose a broker
    outage, and the user-facing article workflow remains available.
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

    def _enqueue_after_commit() -> None:
        try:
            from .tasks import send_article_review_notification

            send_article_review_notification.apply_async(
                args=[article_id, notification_kind, actor_id],
                queue=settings.ARTICLE_REVIEW_NOTIFICATION_CELERY_QUEUE,
            )
        except Exception:
            logger.exception(
                "Could not queue article review notification: article_id=%s kind=%s",
                article_id,
                notification_kind,
            )
            _safe_activity_log(
                ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_FAILED,
                article=article,
                user_id=actor_id,
                details={
                    "notification_kind": notification_kind,
                    "visibility": article.visibility,
                    "reason": "celery_queue_unavailable",
                },
            )
            return

        _safe_activity_log(
            ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_QUEUED,
            article=article,
            user_id=actor_id,
            details={
                "notification_kind": notification_kind,
                "visibility": article.visibility,
            },
        )

    transaction.on_commit(_enqueue_after_commit)
    return True


def _send_messages_individually(
    messages: Iterable[EmailMessage],
) -> tuple[int, int]:
    """Send messages through one relay connection without disclosing recipients.

    A connection failure before the first message is retryable. Once a
    connection exists, individual failures are recorded but not retried because
    SMTP can fail after accepting a message and a retry could duplicate email.
    """
    message_list = list(messages)
    if not message_list:
        return 0, 0

    connection = get_connection(fail_silently=False)
    try:
        opened = connection.open()
        if opened is False and getattr(connection, "connection", None) is None:
            raise RuntimeError("SMTP backend did not open a relay connection.")
    except Exception as exc:
        raise SMTPRelayConnectionError("Unable to open the SMTP relay connection.") from exc

    delivered = 0
    failed = 0
    try:
        for message in message_list:
            try:
                # EmailMessage.send() otherwise opens a new backend per message.
                # Reuse the TLS-authenticated relay connection established above.
                message.connection = connection
                delivered += int(bool(message.send(fail_silently=False)))
            except Exception as exc:
                failed += 1
                logger.error(
                    "Article review notification delivery failed after SMTP connection opened: %s",
                    type(exc).__name__,
                )
    finally:
        try:
            connection.close()
        except Exception:
            logger.warning("Could not close SMTP relay connection cleanly.")

    return delivered, failed


def deliver_article_review_notification(
    article_id: int,
    notification_kind: str,
    submitted_by_user_id: int | None = None,
) -> dict:
    """Resolve recipients and deliver one private notification per reviewer."""
    if not article_review_notifications_enabled():
        return {"status": "disabled"}

    if notification_kind not in _VALID_NOTIFICATION_KINDS:
        logger.error(
            "Ignoring article review notification with unsupported kind: article_id=%s kind=%s",
            article_id,
            notification_kind,
        )
        return {"status": "invalid_kind"}

    article = (
        SuggestedArticle.objects.select_related("owner")
        .filter(pk=article_id)
        .first()
    )
    if article is None:
        return {"status": "article_missing"}

    if not _article_is_still_awaiting_review(article, notification_kind):
        # An approver may have resolved the article before the background worker
        # gets to it. Sending that late notification would be misleading.
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
    messages = [
        EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient.email],
        )
        for recipient in recipients
    ]
    delivered, failed = _send_messages_individually(messages)

    event_type = (
        ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_SENT
        if delivered
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
            "delivered_count": delivered,
            "failed_count": failed,
        },
    )

    return {
        "status": "sent" if delivered else "failed",
        "recipient_count": len(recipients),
        "delivered_count": delivered,
        "failed_count": failed,
    }


def record_article_review_notification_connection_failure(
    article_id: int,
    notification_kind: str,
    submitted_by_user_id: int | None = None,
) -> None:
    """Audit the final relay-connection failure after Celery retries are exhausted."""
    article = SuggestedArticle.objects.filter(pk=article_id).first()
    if article is None:
        return

    _safe_activity_log(
        ActivityLog.EventType.ARTICLE_REVIEW_NOTIFICATION_FAILED,
        article=article,
        user_id=submitted_by_user_id,
        details={
            "notification_kind": notification_kind,
            "visibility": article.visibility,
            "reason": "smtp_connection_unavailable_after_retries",
        },
    )

