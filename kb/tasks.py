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
