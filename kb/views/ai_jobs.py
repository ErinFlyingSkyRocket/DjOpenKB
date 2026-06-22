"""Temporary, permission-scoped background jobs for the Ask OpenKB AI widget.

The job record is intentionally kept in Django's shared cache (Redis in
production) rather than in the database. Question and answer content are
Fernet-encrypted before being cached so Redis persistence/AOF files do not hold
plain-text prompts or internal AI answers. Celery receives only the opaque job
ID; it loads the encrypted prompt after it starts.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import translation
from django.utils.translation import gettext as _

from ..crypto import decrypt_value, encrypt_value
from ..permissions import user_can_view_articles, user_can_view_internal_articles
from .services import user_can_access_main_site
from .services_ai import (
    OpenKBAIOverloaded,
    answer_indicates_no_openkb_match,
    build_openkb_article_recommendation_answer,
    build_openkb_small_talk_answer,
    clean_openkb_ai_answer,
    clean_openkb_ai_error_message,
    find_related_openkb_articles,
    is_openkb_small_talk_request,
    openkb_ai_output_indicates_error,
    redact_openkb_debug_text,
    run_openkb_query,
    should_show_openkb_related_articles,
)


logger = logging.getLogger(__name__)

JOB_VERSION = 1
JOB_KEY_PREFIX = "openkb_ai:job:"
JOB_LOCK_PREFIX = "openkb_ai:job-lock:"
JOB_LOCK_TIMEOUT_SECONDS = 10
JOB_LOCK_WAIT_SECONDS = 0.75
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "revoked"}


def _job_cache_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def _job_lock_key(job_id: str) -> str:
    return f"{JOB_LOCK_PREFIX}{job_id}"


def _now() -> float:
    return time.time()


def _valid_job_id(job_id: str) -> bool:
    try:
        return bool(uuid.UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError):
        return False


def _remaining_timeout(job: dict[str, Any]) -> int:
    expires_at = float(job.get("expires_at") or 0)
    return max(1, int(math.ceil(expires_at - _now())))


def _save_job(job: dict[str, Any]) -> None:
    timeout = _remaining_timeout(job)
    cache.set(_job_cache_key(str(job["id"])), job, timeout=timeout)


def _acquire_job_update_lock(job_id: str) -> str | None:
    """Take a short Redis-backed lock before mutating one job record.

    Django's cache API does not provide compare-and-set updates. Without a
    small per-job lock, a late worker result could race a browser clear action
    and overwrite the cancelled status. ``cache.add`` is atomic with Redis and
    is also safe for the single-process local cache fallback used in tests.
    """
    token = uuid.uuid4().hex
    deadline = _now() + JOB_LOCK_WAIT_SECONDS
    lock_key = _job_lock_key(job_id)
    while _now() < deadline:
        if cache.add(lock_key, token, timeout=JOB_LOCK_TIMEOUT_SECONDS):
            return token
        time.sleep(0.025)
    return None


def _release_job_update_lock(job_id: str, token: str | None) -> None:
    if not token:
        return
    lock_key = _job_lock_key(job_id)
    try:
        if cache.get(lock_key) == token:
            cache.delete(lock_key)
    except Exception:
        logger.exception("Failed to release OpenKB AI job update lock: job_id=%s", job_id)


def get_openkb_ai_job(job_id: str) -> dict[str, Any] | None:
    """Return a non-expired temporary job record, or ``None``."""
    if not _valid_job_id(job_id):
        return None

    job = cache.get(_job_cache_key(str(job_id)))
    if not isinstance(job, dict) or job.get("version") != JOB_VERSION:
        return None

    if _remaining_timeout(job) <= 0:
        cache.delete(_job_cache_key(str(job_id)))
        return None
    return job


def _encode_result(payload: dict[str, Any]) -> str:
    return encrypt_value(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _decode_result(job: dict[str, Any]) -> dict[str, Any]:
    encrypted = str(job.get("result_encrypted") or "")
    raw = decrypt_value(encrypted)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _set_terminal_result(job_id: str, status: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Store a terminal result unless the job was already cancelled/revoked."""
    token = _acquire_job_update_lock(job_id)
    if not token:
        return get_openkb_ai_job(job_id)
    try:
        job = get_openkb_ai_job(job_id)
        if not job:
            return None
        if job.get("status") in {"cancelled", "revoked"}:
            return job

        job["status"] = status
        job["finished_at"] = _now()
        job["result_encrypted"] = _encode_result(payload)
        _save_job(job)
        return job
    finally:
        _release_job_update_lock(job_id, token)


def _set_job_status(job_id: str, status: str) -> dict[str, Any] | None:
    token = _acquire_job_update_lock(job_id)
    if not token:
        return get_openkb_ai_job(job_id)
    try:
        job = get_openkb_ai_job(job_id)
        if not job:
            return None
        if job.get("status") in TERMINAL_STATUSES:
            return job
        job["status"] = status
        if status == "running":
            job["started_at"] = _now()
        _save_job(job)
        return job
    finally:
        _release_job_update_lock(job_id, token)


def _job_result_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("id"),
        "status": job.get("status", "queued"),
        **_decode_result(job),
    }


def enqueue_openkb_ai_job(*, user, question: str, include_internal: bool, language_code: str | None) -> dict[str, Any]:
    """Create a short-lived encrypted job and enqueue its opaque ID in Celery."""
    now = _now()
    ttl = max(300, int(getattr(settings, "OPENKB_AI_JOB_TTL_SECONDS", 1800)))
    job_id = str(uuid.uuid4())
    job = {
        "version": JOB_VERSION,
        "id": job_id,
        "owner_user_id": int(user.pk),
        "include_internal": bool(include_internal),
        "language_code": str(language_code or settings.LANGUAGE_CODE),
        "question_encrypted": encrypt_value(question),
        "status": "queued",
        "created_at": now,
        "expires_at": now + ttl,
    }
    _save_job(job)

    try:
        # Import lazily so normal Django startup does not depend on task discovery
        # before Celery is configured.
        from ..tasks import run_openkb_ai_job

        run_openkb_ai_job.apply_async(
            args=[job_id],
            task_id=job_id,
            queue=getattr(settings, "OPENKB_AI_CELERY_QUEUE", "openkb_ai"),
            expires=ttl,
        )
    except Exception:
        cache.delete(_job_cache_key(job_id))
        raise

    return {"job_id": job_id, "status": "queued"}


def get_openkb_ai_job_for_user(job_id: str, user) -> dict[str, Any] | None:
    """Return a job only when it belongs to the currently signed-in user."""
    job = get_openkb_ai_job(job_id)
    if not job or str(job.get("owner_user_id")) != str(getattr(user, "pk", "")):
        return None
    return job


def job_scope_is_currently_allowed(job: dict[str, Any], user) -> bool:
    """Re-check current account and scope access before any result is revealed."""
    if not user_can_access_main_site(user) or not user_can_view_articles(user):
        return False
    if job.get("include_internal") and not user_can_view_internal_articles(user):
        return False
    return True


def revoke_openkb_ai_job_for_access(job_id: str) -> dict[str, Any] | None:
    """Hide a queued/completed result immediately after role access is removed."""
    return _set_terminal_result(
        job_id,
        "revoked",
        {"error": _("You do not have permission to view articles.")},
    )


def cancel_openkb_ai_job(job_id: str, user) -> bool:
    """Cancel a queued/running job without terminating a worker process.

    A running OpenKB subprocess is allowed to finish safely, but its answer is
    discarded because the record remains cancelled. The per-job update lock
    prevents a late worker result from overwriting this cancelled status.
    """
    token = _acquire_job_update_lock(job_id)
    if not token:
        return False
    try:
        job = get_openkb_ai_job_for_user(job_id, user)
        if not job:
            return False
        if job.get("status") in TERMINAL_STATUSES:
            return True

        job["status"] = "cancelled"
        job["finished_at"] = _now()
        job.pop("result_encrypted", None)
        _save_job(job)
        return True
    finally:
        _release_job_update_lock(job_id, token)


def get_openkb_ai_job_response(job_id: str, user) -> dict[str, Any] | None:
    """Return a permission-safe status/result payload for browser polling."""
    job = get_openkb_ai_job_for_user(job_id, user)
    if not job:
        return None

    if not job_scope_is_currently_allowed(job, user):
        job = revoke_openkb_ai_job_for_access(job_id) or job

    return _job_result_payload(job)


def _get_currently_authorised_job_user(job: dict[str, Any]):
    """Resolve the job owner and enforce current permissions in the worker."""
    user = get_user_model().objects.filter(pk=job.get("owner_user_id")).first()
    if not user or not job_scope_is_currently_allowed(job, user):
        return None
    return user


def _complete_with_article_fallback(job_id: str, question: str, user, *, status: str = "completed") -> None:
    related_articles = find_related_openkb_articles(question, limit=3, user=user)
    if related_articles:
        _set_terminal_result(
            job_id,
            status,
            {
                "answer": build_openkb_article_recommendation_answer(question, related_articles),
                "related_articles": related_articles,
                "show_related_articles": True,
            },
        )
        return

    _set_terminal_result(
        job_id,
        "failed",
        {
            "error": _(
                "OpenKB AI could not complete the request. Please try again later or contact IT support if the issue persists."
            ),
            "related_articles": [],
            "show_related_articles": False,
        },
    )


def execute_openkb_ai_job(job_id: str) -> dict[str, Any]:
    """Run one scoped OpenKB query from a Celery worker.

    This is deliberately idempotent from the browser's perspective. A worker
    retry may repeat a provider call after a crash, but it writes only one
    encrypted job result and the browser deduplicates by ``job_id``.
    """
    job = get_openkb_ai_job(job_id)
    if not job:
        return {"status": "expired"}
    if job.get("status") in TERMINAL_STATUSES:
        return {"status": job.get("status")}

    _set_job_status(job_id, "running")
    job = get_openkb_ai_job(job_id)
    if not job or job.get("status") in TERMINAL_STATUSES:
        return {"status": "cancelled"}

    user = _get_currently_authorised_job_user(job)
    language_code = job.get("language_code") or settings.LANGUAGE_CODE

    with translation.override(language_code):
        if not user:
            revoke_openkb_ai_job_for_access(job_id)
            return {"status": "revoked"}

        question = decrypt_value(str(job.get("question_encrypted") or "")).strip()
        if not question:
            _set_terminal_result(
                job_id,
                "failed",
                {
                    "error": _(
                        "OpenKB AI could not complete the request. Please try again later or contact IT support if the issue persists."
                    ),
                    "related_articles": [],
                    "show_related_articles": False,
                },
            )
            return {"status": "failed"}

        if is_openkb_small_talk_request(question):
            _set_terminal_result(
                job_id,
                "completed",
                {
                    "answer": build_openkb_small_talk_answer(question),
                    "related_articles": [],
                    "show_related_articles": False,
                },
            )
            return {"status": "completed"}

        if not settings.OPENKB_DATA_DIR.exists():
            _set_terminal_result(
                job_id,
                "failed",
                {
                    "error": _("OpenKB data folder not found. Check OPENKB_DATA_DIR in settings.py."),
                    "related_articles": [],
                    "show_related_articles": False,
                },
            )
            return {"status": "failed"}

        try:
            raw_answer = run_openkb_query(question, include_internal=bool(job.get("include_internal")))
            related_articles = find_related_openkb_articles(question, limit=3, user=user)

            if openkb_ai_output_indicates_error(raw_answer):
                logger.warning(
                    "OpenKB AI background provider error: job_id=%s user_id=%s question_length=%s raw_output=%r",
                    job_id,
                    user.pk,
                    len(question),
                    redact_openkb_debug_text(raw_answer),
                )
                _complete_with_article_fallback(job_id, question, user)
                return {"status": "completed"}

            answer = clean_openkb_ai_answer(raw_answer)
            if answer_indicates_no_openkb_match(answer):
                answer = _("The knowledge base does not contain matching information about that topic.")
                related_articles = []
            else:
                related_articles = find_related_openkb_articles(
                    question,
                    limit=3,
                    user=user,
                    answer=answer,
                )

            if not answer:
                answer = _(
                    "OpenKB AI could not produce a clear answer for that question. "
                    "Please try rephrasing it or contact IT support if the issue persists."
                )

            show_related_articles = should_show_openkb_related_articles(
                question,
                answer,
                related_articles=related_articles,
            )
            _set_terminal_result(
                job_id,
                "completed",
                {
                    "answer": answer,
                    "related_articles": related_articles if show_related_articles else [],
                    "show_related_articles": bool(show_related_articles),
                },
            )
            return {"status": "completed"}

        except OpenKBAIOverloaded:
            # Let the Celery task reschedule this short-lived job instead of
            # returning an unnecessary "busy" error to a queued user.
            _set_job_status(job_id, "queued")
            raise
        except FileNotFoundError:
            _complete_with_article_fallback(job_id, question, user)
        except Exception as error:
            logger.exception(
                "OpenKB AI background query failed: job_id=%s user_id=%s question_length=%s error=%r",
                job_id,
                user.pk,
                len(question),
                redact_openkb_debug_text(error),
            )
            related_articles = find_related_openkb_articles(question, limit=3, user=user)
            if related_articles:
                _set_terminal_result(
                    job_id,
                    "completed",
                    {
                        "answer": build_openkb_article_recommendation_answer(question, related_articles),
                        "related_articles": related_articles,
                        "show_related_articles": True,
                    },
                )
            else:
                _set_terminal_result(
                    job_id,
                    "failed",
                    {
                        "error": clean_openkb_ai_error_message(error),
                        "related_articles": [],
                        "show_related_articles": False,
                    },
                )

    return {"status": "completed"}


def mark_openkb_ai_job_retry_exhausted(job_id: str) -> None:
    """Store a normal user-facing error after Celery cannot acquire a slot."""
    job = get_openkb_ai_job(job_id)
    if not job:
        return
    language_code = job.get("language_code") or settings.LANGUAGE_CODE
    with translation.override(language_code):
        _set_terminal_result(
            job_id,
            "failed",
            {
                "error": _("OpenKB AI is currently handling other questions. Please try again shortly."),
                "related_articles": [],
                "show_related_articles": False,
            },
        )
