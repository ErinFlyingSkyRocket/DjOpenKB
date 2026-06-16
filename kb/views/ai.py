from .services import *


def _openkb_ai_user_context(request):
    return {
        "identifier": get_openkb_ai_rate_identifier(request),
        "ip": get_client_ip(request),
        "user_id": request.user.pk if request.user.is_authenticated else "anonymous",
    }


def _article_recommendation_response(question, related_articles=None, status=200):
    """Return a fallback chat response based on local published articles only."""
    if related_articles is None:
        related_articles = find_related_openkb_articles(question, limit=5, user=request.user)

    return JsonResponse(
        {
            "answer": build_openkb_article_recommendation_answer(question, related_articles),
            "related_articles": related_articles,
            "show_related_articles": bool(related_articles),
        },
        status=status,
    )


@article_view_required
@require_POST
def ask_openkb_ai(request):
    """Answer questions through OpenKB/Gemini first, then attach relevant articles.

    The chatbox should behave like an AI assistant first. Prompts such as
    "articles about X" no longer short-circuit into a hardcoded local-only
    article search. The AI answer is returned whenever the provider is
    available, and relevant published article suggestions are attached below it
    when matching articles exist.
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
                "error": "Too many OpenKB AI questions. Please wait before trying again.",
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
            "ai_scope": "internal_plus_public" if user_can_view_internal_articles(request.user) else "public",
        },
    )

    if not settings.OPENKB_DATA_DIR.exists():
        return JsonResponse({
            "error": "OpenKB data folder not found. Check OPENKB_DATA_DIR in settings.py.",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    # Keep greetings/status checks local so the OpenKB CLI does not answer from
    # internal runtime logs or previous operation records.
    if is_openkb_small_talk_request(question):
        return JsonResponse({
            "answer": build_openkb_small_talk_answer(question),
            "related_articles": [],
            "show_related_articles": False,
        })

    try:
        include_internal = user_can_view_internal_articles(request.user)
        raw_answer = run_openkb_query(question, include_internal=include_internal)
        related_articles = find_related_openkb_articles(question, limit=5, user=request.user)

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
            if related_articles:
                return _article_recommendation_response(question, related_articles=related_articles)
            return JsonResponse({
                "error": clean_openkb_ai_error_message(raw_answer),
                "related_articles": [],
                "show_related_articles": False,
            }, status=503)

        answer = clean_openkb_ai_answer(raw_answer)

        if answer_indicates_no_openkb_match(answer):
            answer = "The knowledge base does not contain matching information about that topic."
            related_articles = []

        if not answer:
            answer = (
                "OpenKB AI could not produce a clear answer for that question. "
                "Please try rephrasing it or contact IT support if the issue persists."
            )

        show_related_articles = should_show_openkb_related_articles(
            question,
            answer,
            related_articles=related_articles,
        )

        return JsonResponse({
            "answer": answer,
            "related_articles": related_articles if show_related_articles else [],
            "show_related_articles": show_related_articles,
        })

    except FileNotFoundError:
        related_articles = find_related_openkb_articles(question, limit=5, user=request.user)
        if related_articles:
            return _article_recommendation_response(question, related_articles=related_articles)
        return JsonResponse({
            "error": "OpenKB CLI not found. Run: python -m pip install -e OpenKB-main",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    except OpenKBAIOverloaded:
        ctx = _openkb_ai_user_context(request)
        logger.warning(
            "OpenKB AI concurrency limit reached: identifier=%s ip=%s user_id=%s question_length=%s",
            ctx["identifier"], ctx["ip"], ctx["user_id"], len(question),
        )
        related_articles = find_related_openkb_articles(question, limit=5, user=request.user)
        if related_articles:
            return _article_recommendation_response(question, related_articles=related_articles, status=503)
        return JsonResponse({
            "error": "OpenKB AI is currently handling other questions. Please try again shortly.",
            "related_articles": [],
            "show_related_articles": False,
        }, status=503)

    except subprocess.TimeoutExpired:
        ctx = _openkb_ai_user_context(request)
        logger.warning(
            "OpenKB AI query timed out: identifier=%s ip=%s user_id=%s question_length=%s",
            ctx["identifier"], ctx["ip"], ctx["user_id"], len(question),
        )
        related_articles = find_related_openkb_articles(question, limit=5, user=request.user)
        if related_articles:
            return _article_recommendation_response(question, related_articles=related_articles)
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
        related_articles = find_related_openkb_articles(question, limit=5, user=request.user)
        if related_articles:
            return _article_recommendation_response(question, related_articles=related_articles)
        return JsonResponse({
            "error": clean_openkb_ai_error_message(error),
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)
