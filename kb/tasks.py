"""Celery tasks for short-lived background work."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from .views.ai_jobs import (
    execute_openkb_ai_job,
    mark_openkb_ai_job_retry_exhausted,
)
from .views.services_ai import OpenKBAIOverloaded
from .notifications import (
    SMTPRelayConnectionError,
    deliver_article_review_notification,
    record_article_review_notification_connection_failure,
)


logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="kb.tasks.run_openkb_ai_job",
    queue="openkb_ai",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_openkb_ai_job(self, job_id: str):
    """Run an OpenKB AI job outside the web/Gunicorn request lifecycle."""
    try:
        return execute_openkb_ai_job(job_id)
    except OpenKBAIOverloaded as exc:
        max_retries = max(0, int(getattr(settings, "OPENKB_AI_JOB_BUSY_RETRIES", 12)))
        if self.request.retries >= max_retries:
            mark_openkb_ai_job_retry_exhausted(job_id)
            return {"status": "failed"}

        retry_delay = min(30, 2 + (self.request.retries * 2))
        logger.info(
            "OpenKB AI job waiting for a global concurrency slot: job_id=%s retry=%s delay=%ss",
            job_id,
            self.request.retries + 1,
            retry_delay,
        )
        raise self.retry(exc=exc, countdown=retry_delay, max_retries=max_retries)


@shared_task(
    bind=True,
    name="kb.tasks.send_article_review_notification",
    queue="article_review_notifications",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_article_review_notification(
    self,
    article_id: int,
    notification_kind: str,
    submitted_by_user_id: int | None = None,
):
    """Send reviewer mail outside the web request; retry only unsafe relay outages."""
    try:
        return deliver_article_review_notification(
            article_id,
            notification_kind,
            submitted_by_user_id,
        )
    except SMTPRelayConnectionError as exc:
        max_retries = max(
            0,
            int(getattr(settings, "ARTICLE_REVIEW_NOTIFICATION_MAX_RETRIES", 3)),
        )
        if self.request.retries >= max_retries:
            logger.error(
                "SMTP relay unavailable after all article-review notification retries: "
                "article_id=%s notification_kind=%s",
                article_id,
                notification_kind,
            )
            record_article_review_notification_connection_failure(
                article_id,
                notification_kind,
                submitted_by_user_id,
            )
            return {"status": "relay_unavailable"}

        base_delay = max(
            5,
            int(
                getattr(
                    settings,
                    "ARTICLE_REVIEW_NOTIFICATION_RETRY_DELAY_SECONDS",
                    30,
                )
            ),
        )
        retry_delay = min(3600, base_delay * (self.request.retries + 1))
        logger.warning(
            "SMTP relay connection unavailable; retrying article review notification: "
            "article_id=%s retry=%s delay=%ss",
            article_id,
            self.request.retries + 1,
            retry_delay,
        )
        raise self.retry(exc=exc, countdown=retry_delay, max_retries=max_retries)

