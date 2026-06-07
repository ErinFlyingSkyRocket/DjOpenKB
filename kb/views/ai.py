from .services import *


def _openkb_ai_user_context(request):
    return {
        "identifier": get_openkb_ai_rate_identifier(request),
        "ip": get_client_ip(request),
        "user_id": request.user.pk if request.user.is_authenticated else "anonymous",
    }


def _article_recommendation_response(question, related_articles=None, status=200):
    """Return a chat response based on local published articles only."""
    if related_articles is None:
        related_articles = find_related_openkb_articles(question, limit=5)

    return JsonResponse(
        {
            "answer": build_openkb_article_recommendation_answer(question, related_articles),
            "related_articles": related_articles,
            "show_related_articles": bool(related_articles),
        },
        status=status,
    )


@require_POST
def ask_openkb_ai(request):
    """Answer OpenKB AI questions and always allow public article recommendations.

    Article/link recommendation requests are served directly from the local
    published-article database so they work for anonymous users and signed-in
    users even when the external LLM provider is busy. General Q&A still tries
    the real OpenKB/Gemini flow first, then falls back to local article links.
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
        return JsonResponse({"error": "Please type a question first.", "related_articles": [], "show_related_articles": False}, status=400)

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

    # Do not send greetings/status checks into OpenKB. The OpenKB CLI keeps
    # internal runtime logs, and generic prompts can cause the model to talk
    # about those logs instead of published articles.
    if is_openkb_small_talk_request(question):
        return JsonResponse({
            "answer": build_openkb_small_talk_answer(question),
            "related_articles": [],
            "show_related_articles": False,
        })

    # If the user is asking for article/source/latest recommendations, answer
    # directly from the published Django article database. This avoids exposing
    # OpenKB internal runtime data and keeps the response predictable.
    if is_openkb_article_recommendation_request(question) or is_openkb_latest_article_request(question):
        return _article_recommendation_response(question)

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
            if is_openkb_article_recommendation_request(question) and related_articles:
                return _article_recommendation_response(question, related_articles=related_articles)
            return JsonResponse({
                "error": clean_openkb_ai_error_message(raw_answer),
                "related_articles": [],
                "show_related_articles": False,
            }, status=503)

        answer = clean_openkb_ai_answer(raw_answer)

        if not answer:
            if related_articles:
                answer = build_openkb_article_recommendation_answer(question, related_articles)
            else:
                answer = "I can only answer from published knowledge-base articles. Please ask about an article topic or request relevant articles."

        # Even if OpenKB says it has no answer, still show matching local links.
        # This avoids hiding useful articles when the external provider is vague.
        show_related_articles = bool(related_articles)

        return JsonResponse({
            "answer": answer,
            "related_articles": related_articles if show_related_articles else [],
            "show_related_articles": show_related_articles,
        })

    except FileNotFoundError:
        related_articles = find_related_openkb_articles(question, limit=5)
        if related_articles:
            return _article_recommendation_response(question, related_articles=related_articles)
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
        related_articles = find_related_openkb_articles(question, limit=5)
        if related_articles:
            return _article_recommendation_response(question, related_articles=related_articles)
        return JsonResponse({
            "error": "OpenKB AI query timed out. Try a shorter question or a more specific article keyword.",
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
        related_articles = find_related_openkb_articles(question, limit=5)
        if related_articles:
            return _article_recommendation_response(question, related_articles=related_articles)
        return JsonResponse({
            "error": clean_openkb_ai_error_message(error),
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)
