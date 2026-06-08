from .services import *


def _openkb_ai_user_context(request):
    return {
        "identifier": get_openkb_ai_rate_identifier(request),
        "ip": get_client_ip(request),
        "user_id": request.user.pk if request.user.is_authenticated else "anonymous",
    }


@require_POST
def ask_openkb_ai(request):
    """Answer OpenKB AI questions through the real OpenKB/Gemini flow.

    The chatbox should behave like an AI assistant first. It no longer short-circuits
    prompts such as "articles about X" into a hardcoded local article-search answer.
    Published article links are still attached when there are relevant matches, but
    the main answer always comes from OpenKB AI whenever the provider is available.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST request required"}, status=405)

    allowed, retry_after = check_openkb_ai_rate_limit(request)
    if not allowed:
        log_activity(
            request,
            ActivityLog.EventType.AI_RATE_LIMITED,
            details={
                "identifier": get_openkb_ai_rate_identifier(request),
                "retry_after_seconds": retry_after,
            },
        )
        return JsonResponse(
            {
                "error": "Too many OpenKB AI questions. Please wait a few minutes before trying again.",
                "retry_after_seconds": retry_after,
                "related_articles": [],
                "show_related_articles": False,
            },
            status=429,
        )

    question = request.POST.get("question", "").strip()

    if not question:
        return JsonResponse(
            {
                "error": "Please type a question first.",
                "related_articles": [],
                "show_related_articles": False,
            },
            status=400,
        )

    max_prompt_chars = settings.OPENKB_AI_MAX_PROMPT_CHARS
    if len(question) > max_prompt_chars:
        ctx = _openkb_ai_user_context(request)
        logger.info(
            "OpenKB AI prompt rejected because it is too long: identifier=%s ip=%s user_id=%s length=%s max_length=%s",
            ctx["identifier"], ctx["ip"], ctx["user_id"], len(question), max_prompt_chars,
        )
        return JsonResponse(
            {
                "error": f"Question is too long. Please keep it under {max_prompt_chars} characters.",
                "related_articles": [],
                "show_related_articles": False,
            },
            status=400,
        )

    log_activity(
        request,
        ActivityLog.EventType.AI_QUESTION,
        details={
            "question_length": len(question),
            "question_preview": redact_openkb_debug_text(question, max_chars=200),
            "identifier": get_openkb_ai_rate_identifier(request),
            "authenticated": request.user.is_authenticated,
        },
    )

    if not settings.OPENKB_DATA_DIR.exists():
        return JsonResponse({
            "error": "OpenKB data folder not found. Check OPENKB_DATA_DIR in settings.py.",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    try:
        raw_answer = run_openkb_query(question)
        related_articles = find_related_openkb_articles(question, limit=5)

        if openkb_ai_output_indicates_error(raw_answer):
            ctx = _openkb_ai_user_context(request)
            logger.warning(
                "OpenKB AI returned provider error output: identifier=%s ip=%s user_id=%s question_length=%s output_length=%s raw_output=%r",
                ctx["identifier"],
                ctx["ip"],
                ctx["user_id"],
                len(question),
                len(raw_answer or ""),
                redact_openkb_debug_text(raw_answer),
            )
            return JsonResponse({
                "error": clean_openkb_ai_error_message(raw_answer),
                "related_articles": [],
                "show_related_articles": False,
            }, status=503)

        answer = clean_openkb_ai_answer(raw_answer)

        if not answer:
            answer = (
                "OpenKB AI could not produce a clear answer for that question. "
                "Please try rephrasing it or contact IT support if the issue persists."
            )

        return JsonResponse({
            "answer": answer,
            "related_articles": related_articles if related_articles else [],
            "show_related_articles": bool(related_articles),
        })

    except FileNotFoundError:
        return JsonResponse({
            "error": "OpenKB CLI not found. Run: python -m pip install -e OpenKB-main",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    except subprocess.TimeoutExpired:
        ctx = _openkb_ai_user_context(request)
        logger.warning(
            "OpenKB AI query timed out: identifier=%s ip=%s user_id=%s question_length=%s",
            ctx["identifier"], ctx["ip"], ctx["user_id"], len(question),
        )
        return JsonResponse({
            "error": "OpenKB AI took too long to respond. Please try again later or contact IT support if the issue persists.",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    except Exception as error:
        ctx = _openkb_ai_user_context(request)
        logger.exception(
            "OpenKB AI query failed: identifier=%s ip=%s user_id=%s question_length=%s error=%r",
            ctx["identifier"],
            ctx["ip"],
            ctx["user_id"],
            len(question),
            redact_openkb_debug_text(error),
        )
        return JsonResponse({
            "error": clean_openkb_ai_error_message(error),
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)
