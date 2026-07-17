"""HTTP endpoints for the persistent Ask OpenKB AI widget."""

from django.conf import settings
from django.http import JsonResponse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..models import ActivityLog
from ..permissions import user_can_view_internal_articles
from .ai_jobs import (
    cancel_openkb_ai_job as cancel_openkb_ai_job_record,
    enqueue_openkb_ai_job,
    get_openkb_ai_job_response,
)
from .services import (
    article_view_required,
    check_openkb_ai_rate_limit,
    get_client_ip,
    get_openkb_ai_rate_identifier,
    log_activity,
    logger,
    redact_openkb_debug_text,
)
from .services_ai import consume_openkb_ai_prompt_quota


def _openkb_ai_user_context(request):
    return {
        "identifier": get_openkb_ai_rate_identifier(request),
        "ip": get_client_ip(request),
        "user_id": request.user.pk if request.user.is_authenticated else "anonymous",
    }


@article_view_required
@require_POST
def ask_openkb_ai(request):
    """Queue an OpenKB AI question and return immediately with an opaque job ID.

    Long OpenKB/Gemini work runs in the dedicated Celery worker, so this request
    remains short and navigating away no longer cancels the query.
    """
    question = request.POST.get("question", "").strip()
    if not question:
        return JsonResponse({"error": _("Please type a question first.")}, status=400)

    max_prompt_chars = settings.OPENKB_AI_MAX_PROMPT_CHARS
    if len(question) > max_prompt_chars:
        ctx = _openkb_ai_user_context(request)
        logger.info(
            "OpenKB AI prompt rejected because it is too long: identifier=%s ip=%s user_id=%s length=%s max_length=%s",
            ctx["identifier"],
            ctx["ip"],
            ctx["user_id"],
            len(question),
            max_prompt_chars,
        )
        return JsonResponse(
            {
                "error": _("Question is too long. Please keep it under %(count)d characters.")
                % {"count": max_prompt_chars},
            },
            status=400,
        )

    # Keep the existing short burst/cooldown protection, then reserve one slot
    # from the user's separate fixed 24-hour allowance. Invalid prompts do not
    # consume either control.
    allowed, retry_after = check_openkb_ai_rate_limit(request)
    if not allowed:
        log_activity(
            request,
            ActivityLog.EventType.AI_RATE_LIMITED,
            details={
                "identifier": get_openkb_ai_rate_identifier(request),
                "limit_type": "burst",
                "retry_after_seconds": retry_after,
            },
        )
        return JsonResponse(
            {
                "error": _("Too many OpenKB AI questions. Please wait before trying again."),
                "retry_after_seconds": retry_after,
            },
            status=429,
        )

    prompt_quota = consume_openkb_ai_prompt_quota(request)
    if not prompt_quota.get("allowed"):
        if prompt_quota.get("reason") == "unavailable":
            return JsonResponse(
                {
                    "error": _("OpenKB AI is currently handling other questions. Please try again shortly."),
                },
                status=503,
            )

        log_activity(
            request,
            ActivityLog.EventType.AI_RATE_LIMITED,
            details={
                "identifier": get_openkb_ai_rate_identifier(request),
                "limit_type": "fixed_24_hour_quota",
                "prompt_limit": prompt_quota.get("limit"),
                "prompt_used": prompt_quota.get("used"),
                "retry_after_seconds": prompt_quota.get("retry_after_seconds"),
            },
        )
        return JsonResponse(
            {
                "error": _(
                    "You have reached your OpenKB AI prompt limit for the current 24-hour period. "
                    "Please try again when your limit resets."
                ),
                "retry_after_seconds": prompt_quota.get("retry_after_seconds", 0),
                "prompt_limit": prompt_quota.get("limit", 20),
                "prompt_used": prompt_quota.get("used", 0),
            },
            status=429,
        )

    include_internal = user_can_view_internal_articles(request.user)
    try:
        payload = enqueue_openkb_ai_job(
            user=request.user,
            question=question,
            include_internal=include_internal,
            language_code=translation.get_language(),
        )
    except Exception as error:
        # The accepted prompt remains counted. This keeps the one-operation
        # Redis quota path cheap and prevents repeated queue-outage retries
        # from becoming a resource-abuse bypass.
        ctx = _openkb_ai_user_context(request)
        logger.exception(
            "OpenKB AI background job could not be queued: identifier=%s ip=%s user_id=%s question_length=%s error=%r",
            ctx["identifier"],
            ctx["ip"],
            ctx["user_id"],
            len(question),
            redact_openkb_debug_text(error),
        )
        return JsonResponse(
            {
                "error": _(
                    "OpenKB AI is temporarily unavailable. Please try again later or contact IT support if the issue persists."
                ),
            },
            status=503,
        )

    # Store only operational metadata in the long-lived audit log. The prompt
    # itself remains only in the encrypted, expiring background job record.
    log_activity(
        request,
        ActivityLog.EventType.AI_QUESTION,
        details={
            "question_length": len(question),
            "identifier": get_openkb_ai_rate_identifier(request),
            "authenticated": request.user.is_authenticated,
            "ai_scope": "internal_plus_public" if include_internal else "public",
            "execution": "background_job",
            "prompt_limit": prompt_quota.get("limit"),
            "prompt_used": prompt_quota.get("used"),
            "prompt_remaining": prompt_quota.get("remaining"),
            "prompt_reset_in_seconds": prompt_quota.get("retry_after_seconds"),
        },
    )

    payload.update(
        {
            "prompt_limit": prompt_quota.get("limit"),
            "prompt_remaining": prompt_quota.get("remaining"),
            "prompt_reset_in_seconds": prompt_quota.get("retry_after_seconds"),
        }
    )
    return JsonResponse(payload, status=202)


@article_view_required
def openkb_ai_job_status(request, job_id):
    """Return a job status/result only to its owner after re-checking scope."""
    payload = get_openkb_ai_job_response(job_id, request.user)
    if payload is None:
        # Do not reveal whether an opaque job ID belongs to another account.
        return JsonResponse({"status": "expired"}, status=404)
    return JsonResponse(payload)


@article_view_required
@require_POST
def cancel_openkb_ai_job(request, job_id):
    """Discard a queued/running result when the user clears the browser chat."""
    if not cancel_openkb_ai_job_record(job_id, request.user):
        return JsonResponse({"status": "expired"}, status=404)
    return JsonResponse({"status": "cancelled"})

