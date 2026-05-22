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

    if not settings.OPENKB_DATA_DIR.exists():
        return JsonResponse({
            "error": "OpenKB data folder not found. Check OPENKB_DATA_DIR in settings.py.",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    # Do not handle greetings or test messages with a fake local chatbot reply.
    # They continue into the real OpenKB/Gemini flow. If the provider is down,
    # the fallback below will not recommend random articles because
    # find_related_openkb_articles() filters those simple chat probes out.

    # If the user is explicitly asking for article/source recommendations,
    # do not wait for Gemini/OpenKB CLI. Return local published links quickly.
    if is_openkb_article_recommendation_request(question):
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
            answer = build_openkb_article_recommendation_answer(question, related_articles) if related_articles else "OpenKB AI returned an empty response."

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
