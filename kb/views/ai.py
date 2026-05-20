from .services import *


def ask_openkb_ai(request):
    """Use the real local OpenKB AI query flow."""
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
        logger.info(
            "OpenKB AI prompt rejected because it is too long: identifier=%s ip=%s user_id=%s length=%s max_length=%s",
            get_openkb_ai_rate_identifier(request),
            get_client_ip(request),
            request.user.pk if request.user.is_authenticated else "anonymous",
            len(question),
            max_prompt_chars,
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

    try:
        raw_answer = run_openkb_query(question)

        if openkb_ai_output_indicates_error(raw_answer):
            logger.warning(
                "OpenKB AI returned provider error output: identifier=%s ip=%s user_id=%s question_length=%s output_length=%s",
                get_openkb_ai_rate_identifier(request),
                get_client_ip(request),
                request.user.pk if request.user.is_authenticated else "anonymous",
                len(question),
                len(raw_answer or ""),
            )
            return JsonResponse({
                "error": clean_openkb_ai_error_message(raw_answer),
                "related_articles": [],
                "show_related_articles": False,
            }, status=503)

        answer = clean_openkb_ai_answer(raw_answer)

        if not answer:
            answer = "OpenKB AI returned an empty response."

        related_articles = []
        if not answer_indicates_no_openkb_match(answer):
            related_articles = find_related_openkb_articles(question)

        show_related_articles = should_show_openkb_related_articles(question, answer, related_articles)

        return JsonResponse({
            "answer": answer,
            "related_articles": related_articles if show_related_articles else [],
            "show_related_articles": show_related_articles,
        })

    except FileNotFoundError:
        return JsonResponse({
            "error": "OpenKB CLI not found. Run: python -m pip install -e OpenKB-main",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    except subprocess.TimeoutExpired:
        logger.warning(
            "OpenKB AI query timed out: identifier=%s ip=%s user_id=%s question_length=%s",
            get_openkb_ai_rate_identifier(request),
            get_client_ip(request),
            request.user.pk if request.user.is_authenticated else "anonymous",
            len(question),
        )
        return JsonResponse({
            "error": "OpenKB AI query timed out. Try a shorter question.",
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)

    except Exception as error:
        logger.exception(
            "OpenKB AI query failed: identifier=%s ip=%s user_id=%s question_length=%s",
            get_openkb_ai_rate_identifier(request),
            get_client_ip(request),
            request.user.pk if request.user.is_authenticated else "anonymous",
            len(question),
        )
        return JsonResponse({
            "error": clean_openkb_ai_error_message(error),
            "related_articles": [],
            "show_related_articles": False,
        }, status=500)
