"""Helper functions split out from kb.views.services into kb.views.services_ai.

This module is imported back by services.py so existing imports continue to work.
"""

from .services import *  # noqa: F401,F403
from django.utils.translation import gettext as _, ngettext

def get_openkb_ai_model():
    """Return the configured OpenKB/LiteLLM model name."""
    model = getattr(settings, "OPENKB_AI_MODEL", "gemini/gemini-2.5-flash")
    return (model or "gemini/gemini-2.5-flash").strip()


def scrub_openkb_runtime_log_files(data_dir=None):
    """Remove OpenKB runtime logs from the AI-readable wiki folder."""
    data_dir = Path(data_dir) if data_dir else settings.OPENKB_DATA_DIR
    runtime_files = [
        data_dir / "wiki" / "log.md",
    ]

    for path in runtime_files:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.warning("Could not remove OpenKB runtime log file %s", path)

def ensure_openkb_config_model(data_dir=None):
    """Keep OpenKB's local config.yaml model aligned with Django settings."""
    model = get_openkb_ai_model()
    if not model:
        return

    data_dir = Path(data_dir) if data_dir else settings.OPENKB_DATA_DIR
    config_path = data_dir / ".openkb" / "config.yaml"

    if not config_path.exists() and data_dir != settings.OPENKB_DATA_DIR:
        public_config = settings.OPENKB_DATA_DIR / ".openkb" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if public_config.exists():
            try:
                shutil.copy2(public_config, config_path)
            except OSError:
                return

    if not config_path.exists():
        return

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return

    model_line = f"model: {model}"
    if re.search(r"(?m)^model:\s*.*$", text):
        new_text = re.sub(r"(?m)^model:\s*.*$", model_line, text, count=1)
    else:
        new_text = text.rstrip() + "\n" + model_line + "\n"

    if new_text != text:
        try:
            config_path.write_text(new_text, encoding="utf-8")
        except OSError:
            return



class OpenKBAIOverloaded(RuntimeError):
    """Raised when the global OpenKB AI concurrency limit is full."""


def acquire_openkb_ai_slot():
    """Acquire a short-lived global AI concurrency slot from Django cache.

    cache.add() is atomic on Redis, so this works across all Gunicorn workers.
    With the development LocMem fallback it is only per-process, which is why
    Redis is required when DJANGO_DEBUG=false.
    """
    limit = max(1, int(getattr(settings, "OPENKB_AI_CONCURRENCY_LIMIT", 2)))
    lock_seconds = max(30, int(getattr(settings, "OPENKB_AI_CONCURRENCY_LOCK_SECONDS", 120)))
    token = uuid.uuid4().hex

    for slot in range(limit):
        key = f"openkb_ai:active:{slot}"
        if cache.add(key, token, lock_seconds):
            return key, token

    raise OpenKBAIOverloaded("OpenKB AI is currently busy")


def release_openkb_ai_slot(lock):
    if not lock:
        return
    key, token = lock
    try:
        if cache.get(key) == token:
            cache.delete(key)
    except Exception:
        logger.exception("Failed to release OpenKB AI concurrency lock")


def run_openkb_query(question, *, include_internal=False):
    """Call the bundled OpenKB CLI against the correct scoped data directory.

    The global Redis slot is acquired before synchronising/indexing the on-disk
    OpenKB data. This prevents parallel worker processes from rebuilding the
    same index at the same time and makes the concurrency limit cover the full
    expensive operation, not only the final CLI subprocess.
    """
    lock = acquire_openkb_ai_slot()
    query_data_dir = None
    try:
        if include_internal:
            query_data_dir = sync_internal_openkb_ai_index()
        else:
            init_openkb_storage()
            ensure_openkb_ai_synced()
            query_data_dir = settings.OPENKB_DATA_DIR

        ensure_openkb_config_model(query_data_dir)
        scrub_openkb_runtime_log_files(query_data_dir)

        env = os.environ.copy()

        ai_api_key = getattr(settings, "AI_API_KEY", "")
        if ai_api_key:
            # Development/simple-deployment fallback. Provider-specific keys below
            # take precedence when configured in Vault/env for production.
            env["AI_API_KEY"] = ai_api_key
            env["LLM_API_KEY"] = ai_api_key
            env.setdefault("GEMINI_API_KEY", ai_api_key)
            env.setdefault("OPENAI_API_KEY", ai_api_key)
            env.setdefault("ANTHROPIC_API_KEY", ai_api_key)

        provider_keys = {
            "OPENAI_API_KEY": getattr(settings, "OPENAI_API_KEY", ""),
            "GEMINI_API_KEY": getattr(settings, "GEMINI_API_KEY", ""),
            "ANTHROPIC_API_KEY": getattr(settings, "ANTHROPIC_API_KEY", ""),
        }
        for name, value in provider_keys.items():
            if value:
                env[name] = value

        env["OPENKB_AI_PROVIDER"] = getattr(settings, "OPENKB_AI_PROVIDER", "openkb-cli")
        env["OPENKB_AI_MODEL"] = get_openkb_ai_model()
        env["LITELLM_DROP_PARAMS"] = "true"
        env["DROP_PARAMS"] = "true"
        env["OPENKB_DIR"] = str(query_data_dir)
        env["PYTHONPATH"] = (
            str(settings.OPENKB_BASE_DIR)
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )

        command = (
            "import sys; "
            "import litellm; "
            "litellm.drop_params = True; "
            f"sys.path.insert(0, {str(settings.OPENKB_BASE_DIR)!r}); "
            "from openkb.cli import cli; "
            "cli.main("
            "args=['--kb-dir', sys.argv[2], 'query', sys.argv[1]], "
            "prog_name='openkb', "
            "standalone_mode=False"
            ")"
        )

        result = subprocess.run(
            [sys.executable, "-c", command, question, str(query_data_dir)],
            cwd=str(settings.BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(getattr(settings, "OPENKB_AI_TIMEOUT_SECONDS", 90)),
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or "openkb query failed")

        return result.stdout.strip()
    finally:
        release_openkb_ai_slot(lock)
        # OpenKB may append the current query to wiki/log.md after execution.
        # Remove it again so the next user query cannot read internal log data.
        if query_data_dir:
            scrub_openkb_runtime_log_files(query_data_dir)


OPENKB_AI_SMALL_TALK_PATTERNS = [
    r"^hi+$",
    r"^hi\s+there[!.?]*$",
    r"^hello+$",
    r"^hello\s+there[!.?]*$",
    r"^hey+$",
    r"^hey\s+there[!.?]*$",
    r"^are\s+you\s+there[?.!]*$",
    r"^are\s+you\s+still\s+there[?.!]*$",
    r"^are\s+you\s+still\s+here[?.!]*$",
    r"^you\s+there[?.!]*$",
    r"^test[?.!]*$",
    r"^testing[?.!]*$",
]


def is_openkb_small_talk_request(question):
    """Return True for greetings/status checks that should not trigger article search.

    Without this guard, a short message such as "hi there" can accidentally
    match published articles because generic words appear in article bodies.
    """
    prompt = re.sub(r"\s+", " ", (question or "").strip().lower())
    if not prompt:
        return False

    if len(prompt) <= 80:
        for pattern in OPENKB_AI_SMALL_TALK_PATTERNS:
            if re.fullmatch(pattern, prompt):
                return True

    tokens = tokenize_search_query(prompt)
    return not tokens and len(prompt) <= 80


def build_openkb_small_talk_answer(question=None):
    """Return a normal chat response for greetings/status messages."""
    prompt = ((question or "").strip().lower())
    if "still" in prompt or "there" in prompt or "here" in prompt:
        return _("Yes, I’m here. Ask me a question about the knowledge base, or ask me to recommend published articles.")
    if "test" in prompt:
        return _("OpenKB AI is ready. Ask me a knowledge-base question or request relevant articles.")
    return _("Hello! I’m OpenKB AI. Ask me a question about the knowledge base, or ask me to recommend published articles.")


OPENKB_AI_ARTICLE_INTENT_TERMS = [
    "article", "articles",
    "related article", "related articles",
    "recommend article", "recommend articles",
    "recommendation", "recommendations",
    "source", "sources",
    "reference", "references",
    "link", "links",
    "where can i read", "where to read",
    "documentation", "docs",
    "show article", "show articles",
    "find article", "find articles",
    "relevant article", "relevant articles",
    "anything about", "any article", "any articles",
    "is there any article", "is there any articles",
    "latest article", "latest articles",
    "newest article", "newest articles",
    "anything new", "what is new", "what's new",
]


def is_openkb_article_recommendation_request(question):
    """Return True when the user is mainly asking for article links.

    These requests should be answered from the local published-article database
    first, without waiting for the external LLM provider. This keeps the chatbox
    useful for anonymous users and logged-in users even when Gemini/LiteLLM is
    slow, rate-limited, or unavailable.
    """
    prompt = (question or "").lower()
    return any(term in prompt for term in OPENKB_AI_ARTICLE_INTENT_TERMS)


def is_openkb_latest_article_request(question):
    """Return True when the user asks for latest/newest published articles."""
    prompt = re.sub(r"\s+", " ", (question or "").strip().lower())
    latest_terms = [
        "latest article", "latest articles",
        "newest article", "newest articles",
        "anything new", "what is new", "what's new",
        "any new article", "any new articles",
        "new article", "new articles",
    ]
    return any(term in prompt for term in latest_terms)


def normalize_openkb_article_query(question):
    """Remove chat/request filler words so article search can match titles better."""
    query = (question or "").strip()

    quoted = re.findall(r"[\"']([^\"']{2,})[\"']", query)
    if quoted:
        return " ".join(quoted).strip()

    cleaned = query.lower()
    replacements = [
        "is there any articles about", "is there any article about",
        "are there any articles about", "are there any article about",
        "is there any articles on", "is there any article on",
        "are there any articles on", "are there any article on",
        "any relevant articles on", "any relevant article on",
        "any relevant articles about", "any relevant article about",
        "any articles about", "any article about",
        "any articles on", "any article on",
        "articles about", "article about",
        "articles on", "article on",
        "recommend articles about", "recommend article about",
        "recommend articles on", "recommend article on",
        "find articles about", "find article about",
        "find articles on", "find article on",
        "show articles about", "show article about",
        "show articles on", "show article on",
        "anything about", "something about",
        "relevant article", "relevant articles",
        "published article", "published articles",
        "wiki article", "wiki articles",
        "documentation", "docs",
        "please", "can you", "could you", "help me",
    ]
    for phrase in replacements:
        cleaned = cleaned.replace(phrase, " ")

    cleaned = re.sub(r"[^a-z0-9@._\- ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or query


OPENKB_AI_RECOMMENDATION_EXTRA_STOPWORDS = SEARCH_STOPWORDS | {
    "about", "again", "also", "article", "articles", "assistant", "based",
    "could", "detail", "details", "document", "documents", "find", "give",
    "guide", "help", "information", "kb", "knowledge", "link", "links",
    "need", "openkb", "page", "pages", "provide", "read", "recommend",
    "recommended", "reference", "references", "related", "result", "results",
    "search", "show", "source", "sources", "tell", "topic", "wiki", "would",
}


def _unique_limited_tokens(tokens, limit=80):
    """Return unique useful tokens while preserving the original order."""
    unique = []
    seen = set()
    for token in tokens:
        token = (token or "").strip().lower()
        if not token or token in seen:
            continue
        if token in OPENKB_AI_RECOMMENDATION_EXTRA_STOPWORDS:
            continue
        seen.add(token)
        unique.append(token)
        if len(unique) >= limit:
            break
    return unique


def _article_search_text(value):
    """Normalize searchable article text for phrase matching."""
    value = strip_markdown_for_search(value or "")
    value = re.sub(r"[^a-zA-Z0-9@._\- ]+", " ", value.lower())
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _build_ai_article_match_context(question, answer=None):
    """Build weighted query data for AI article recommendations.

    The public search page intentionally uses title/keyword-only matching so it
    stays predictable. The chatbot needs a stronger helper because OpenKB may
    answer from article body content even when the article title does not share
    the exact user wording. This context remains safe because candidates still
    come from permission-filtered, published Django articles only.
    """
    normalized_question = normalize_openkb_article_query(question)
    question_tokens = _unique_limited_tokens(tokenize_search_query(normalized_question), limit=40)

    answer_tokens = []
    if answer and not answer_indicates_no_openkb_match(answer):
        answer_tokens = _unique_limited_tokens(tokenize_search_query(answer), limit=50)

    combined_tokens = _unique_limited_tokens(question_tokens + answer_tokens, limit=80)

    phrases = []
    for source in [normalized_question, question or ""]:
        phrase = _article_search_text(source)
        if phrase and len(phrase) >= 3 and phrase not in phrases:
            phrases.append(phrase)

    # Quoted text and common "about/on/for X" patterns are often the user's
    # clearest intended article topic. Keep them as additional exact phrases.
    raw_question = question or ""
    for quoted in re.findall(r"[\"']([^\"']{3,80})[\"']", raw_question):
        phrase = _article_search_text(quoted)
        if phrase and phrase not in phrases:
            phrases.append(phrase)

    topic_match = re.search(
        r"(?i)\b(?:about|on|for|regarding|related to)\s+([a-zA-Z0-9@._\- ][a-zA-Z0-9@._\- ]{2,80})",
        raw_question,
    )
    if topic_match:
        phrase = _article_search_text(topic_match.group(1))
        if phrase and phrase not in phrases:
            phrases.append(phrase)

    return {
        "normalized_question": normalized_question,
        "question_tokens": question_tokens,
        "answer_tokens": answer_tokens,
        "combined_tokens": combined_tokens,
        "phrases": phrases[:8],
    }


def _score_ai_related_article(article, match_context):
    """Score how likely an article is the source/helpful link for an AI answer."""
    title_text = _article_search_text(article.get("title") or "")
    keyword_text = _article_search_text(" ".join(article.get("keywords") or []))
    body_text = _article_search_text(article.get("raw_markdown") or "")

    title_tokens = set(tokenize_search_query(title_text))
    keyword_tokens = set(tokenize_search_query(keyword_text))
    body_tokens = set(tokenize_search_query(body_text))

    question_tokens = set(match_context.get("question_tokens") or [])
    answer_tokens = set(match_context.get("answer_tokens") or [])
    combined_tokens = set(match_context.get("combined_tokens") or [])

    if not combined_tokens and not match_context.get("phrases"):
        return 0, []

    score = 0
    matched_terms = set()

    for phrase in match_context.get("phrases") or []:
        if not phrase:
            continue
        if phrase in title_text:
            score += 180
            matched_terms.update(tokenize_search_query(phrase))
        if phrase in keyword_text:
            score += 140
            matched_terms.update(tokenize_search_query(phrase))
        if phrase in body_text:
            score += 70
            matched_terms.update(tokenize_search_query(phrase))

    question_title_overlap = question_tokens & title_tokens
    question_keyword_overlap = question_tokens & keyword_tokens
    question_body_overlap = question_tokens & body_tokens

    answer_title_overlap = answer_tokens & title_tokens
    answer_keyword_overlap = answer_tokens & keyword_tokens
    answer_body_overlap = answer_tokens & body_tokens

    score += len(question_title_overlap) * 45
    score += len(question_keyword_overlap) * 38
    score += len(question_body_overlap) * 12

    # Answer terms are useful for identifying the source article, but they are
    # lower trust than the user's own question because AI answers may contain
    # generic explanatory words.
    score += len(answer_title_overlap) * 20
    score += len(answer_keyword_overlap) * 16
    score += len(answer_body_overlap) * 4

    if question_tokens and question_tokens.issubset(title_tokens | keyword_tokens | body_tokens):
        score += 45

    if len(question_tokens & (title_tokens | keyword_tokens)) >= 2:
        score += 55

    if title_tokens and question_tokens and title_tokens.issubset(question_tokens | answer_tokens):
        score += 35

    matched_terms.update(question_title_overlap)
    matched_terms.update(question_keyword_overlap)
    matched_terms.update(list(question_body_overlap)[:8])
    matched_terms.update(answer_title_overlap)
    matched_terms.update(answer_keyword_overlap)

    # Small tie-breaker only. It should not make unrelated articles appear.
    score += min(int(article.get("views") or 0), 10)
    score += min(int(article.get("likes") or 0), 10)

    return score, list(matched_terms)


def find_related_openkb_articles(question, limit=3, minimum_score=None, user=None, answer=None):
    """Find permission-safe published article links for Ask OpenKB AI.

    Unlike the normal search page, this helper is allowed to inspect article
    body text because it is used after the user already asked the AI a knowledge
    question. The candidate set still comes from get_openkb_wiki_articles(), so
    public users only receive public article links and internal users receive
    internal links only when their role allows it.
    """
    init_openkb_storage()

    if is_openkb_small_talk_request(question):
        return []

    if is_openkb_latest_article_request(question):
        latest_articles = []
        for item in get_openkb_wiki_articles(sort_by_views=False, visibility="all", user=user)[:limit]:
            latest_articles.append({
                "title": item.get("title", str(_("Untitled"))),
                "url": item.get("url") or "#",
                "snippet": item.get("date") or "",
                "visibility": item.get("visibility") or "public",
                "visibility_label": item.get("visibility_label") or "",
            })
        return latest_articles

    match_context = _build_ai_article_match_context(question, answer=answer)

    # If there are no useful searchable terms, do not recommend random articles.
    if not is_openkb_article_recommendation_request(question) and not match_context["combined_tokens"]:
        return []

    if minimum_score is None:
        minimum_score = 22 if is_openkb_article_recommendation_request(question) else 34

    visible_articles = get_openkb_wiki_articles(visibility="all", user=user)
    scored_articles = []

    for item in visible_articles:
        score, matched_terms = _score_ai_related_article(item, match_context)
        if score < int(minimum_score):
            continue

        article = dict(item)
        article["ai_related_score"] = score
        article["ai_matched_terms"] = matched_terms
        scored_articles.append(article)

    # Backward-compatible safety net: exact title/keyword matches from the old
    # helper are still included, but body/answer scoring now decides the final
    # ranking when both methods find results.
    fallback_query = match_context.get("normalized_question") or question
    for item in rank_articles_for_query(visible_articles, fallback_query):
        key = item.get("suggested_id") or item.get("url") or item.get("path") or item.get("title")
        if any((existing.get("suggested_id") or existing.get("url") or existing.get("path") or existing.get("title")) == key for existing in scored_articles):
            continue
        article = dict(item)
        article["ai_related_score"] = max(int(minimum_score), 30)
        article["ai_matched_terms"] = match_context.get("question_tokens") or []
        scored_articles.append(article)

    scored_articles.sort(
        key=lambda item: (
            item.get("ai_related_score") or 0,
            item.get("views") or 0,
            item.get("likes") or 0,
            item.get("date") or "",
        ),
        reverse=True,
    )

    # Do not fill the chat with weak links. Keep only articles that are close
    # enough to the best match, so the result naturally becomes 1 article when
    # only one article is clearly relevant, and up to 3 when several are strong.
    article_intent = is_openkb_article_recommendation_request(question)
    top_score = int(scored_articles[0].get("ai_related_score") or 0) if scored_articles else 0
    relative_multiplier = 0.35 if article_intent else 0.55
    relative_floor = max(int(minimum_score), int(top_score * relative_multiplier))

    results = []
    seen_keys = set()
    snippet_terms = match_context.get("question_tokens") or match_context.get("combined_tokens") or []

    for item in scored_articles:
        key = item.get("suggested_id") or item.get("url") or item.get("path") or item.get("title")
        if not key or key in seen_keys:
            continue

        if int(item.get("ai_related_score") or 0) < relative_floor:
            continue

        seen_keys.add(key)
        snippet = build_search_excerpt(item.get("raw_markdown") or "", snippet_terms)
        if not snippet:
            snippet = item.get("search_excerpt") or ""

        results.append({
            "title": item.get("title", str(_("Untitled"))),
            "url": item.get("url") or (f"/wiki/{item.get('path')}" if item.get("path") else "#"),
            "snippet": snippet,
            "visibility": item.get("visibility") or "public",
            "visibility_label": item.get("visibility_label") or "",
        })

        if len(results) >= limit:
            break

    return results

def build_openkb_article_recommendation_answer(question, related_articles):
    """Create a concise chat answer for article/link requests."""
    if related_articles:
        count = len(related_articles)
        return ngettext(
            "I found %(count)d relevant published article.",
            "I found %(count)d relevant published articles.",
            count,
        ) % {"count": count}

    cleaned_query = normalize_openkb_article_query(question)
    if cleaned_query and cleaned_query != (question or ""):
        return _(
            "I could not find a matching published article for ‘%(query)s’. "
            "Try another keyword or check whether the article is published."
        ) % {"query": cleaned_query}

    return _("I could not find a matching published article. Try another keyword or check whether the article is published.")


def get_client_ip(request):
    """Return the best available client IP behind the trusted Nginx proxy.

    Nginx sets X-Real-IP to the real client address. Prefer that header because
    X-Forwarded-For can contain client-supplied spoofed entries when Nginx appends
    to an existing header. This keeps rate limiting and activity logs more reliable.
    """
    real_ip = (request.META.get("HTTP_X_REAL_IP") or "").strip()
    if real_ip:
        return real_ip

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        # Use the last non-empty address because Nginx's proxy_add_x_forwarded_for
        # appends the immediate client address to the right-hand side.
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[-1]

    return request.META.get("REMOTE_ADDR", "unknown")


def log_activity(request, event_type, article=None, user=None, details=None):
    """Write a general activity/audit event without breaking the user action.

    This is intentionally best-effort: if logging fails, the article/vote/admin
    workflow should still continue, while the exception is written to Django logs.
    """
    try:
        actor = user
        if actor is None and request is not None and getattr(request, "user", None) is not None:
            actor = request.user if request.user.is_authenticated else None

        username = ""
        if actor is not None and getattr(actor, "is_authenticated", False):
            username = actor.get_username() or getattr(actor, "email", "") or ""

        article_id = None
        article_title = ""
        article_status = ""
        article_owner_user_id = None
        article_owner_username = ""
        article_owner_name = ""
        article_owner_email = ""
        article_owner_account_type = ""

        if article is not None:
            article_id = getattr(article, "pk", None)
            article_title = (getattr(article, "title", "") or "")[:255]
            article_status = getattr(article, "status", "") or ""

            # Store the article owner's identity as a historical snapshot.
            # This lets audit logs remain readable even if the article or user
            # is later deleted, and separates "actor" from "article owner".
            article_owner_user_id = getattr(article, "owner_id", None)
            article_owner_username = (getattr(article, "author_username_snapshot", "") or "")[:255]
            article_owner_name = (getattr(article, "author_name_snapshot", "") or "")[:255]
            article_owner_email = (getattr(article, "author_email_snapshot", "") or "")[:254]
            article_owner_account_type = (getattr(article, "author_account_type_snapshot", "") or "")[:50]

            owner = getattr(article, "owner", None)
            if owner is not None:
                article_owner_username = (
                    owner.get_username()
                    or getattr(owner, "email", "")
                    or article_owner_username
                    or ""
                )[:255]
                article_owner_email = (getattr(owner, "email", "") or article_owner_email or "")[:254]
                owner_name = (owner.get_full_name() or "").strip()
                if owner_name:
                    article_owner_name = owner_name[:255]
                profile = getattr(owner, "kb_profile", None)
                if profile is not None and hasattr(profile, "get_account_type_display"):
                    article_owner_account_type = (profile.get_account_type_display() or article_owner_account_type or "")[:50]

        ActivityLog.objects.create(
            event_type=event_type,
            user=actor if getattr(actor, "pk", None) else None,
            username=username,
            article_id=article_id,
            article_title=article_title,
            article_status=article_status,
            article_owner_user_id_snapshot=article_owner_user_id,
            article_owner_username_snapshot=article_owner_username,
            article_owner_name_snapshot=article_owner_name,
            article_owner_email_snapshot=article_owner_email,
            article_owner_account_type_snapshot=article_owner_account_type,
            ip_address=get_client_ip(request) if request is not None else None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request is not None else ""),
            path=(request.get_full_path()[:500] if request is not None else ""),
            request_method=(request.method[:10] if request is not None else ""),
            details=details or {},
        )
    except Exception:
        logger.exception("Failed to write activity log event_type=%s", event_type)


def get_openkb_ai_rate_identifier(request):
    """Rate-limit logged-in users by user id, anonymous visitors by IP."""
    if request.user.is_authenticated:
        return f"user:{request.user.pk}"
    return f"ip:{get_client_ip(request)}"


def check_openkb_ai_rate_limit(request):
    """Return (allowed, retry_after_seconds). Log when a user/IP is blocked."""
    identifier = get_openkb_ai_rate_identifier(request)
    now = int(time.time())

    window_seconds = int(settings.OPENKB_AI_RATE_LIMIT_WINDOW_SECONDS)
    max_requests = int(settings.OPENKB_AI_RATE_LIMIT_MAX_REQUESTS)
    block_seconds = int(settings.OPENKB_AI_RATE_LIMIT_BLOCK_SECONDS)

    block_key = f"openkb_ai:block:{identifier}"
    blocked_until = cache.get(block_key)
    if blocked_until:
        retry_after = max(1, int(blocked_until) - now)
        logger.warning(
            "OpenKB AI blocked request while temporary block is active: identifier=%s ip=%s user_id=%s retry_after_seconds=%s",
            identifier,
            get_client_ip(request),
            request.user.pk if request.user.is_authenticated else "anonymous",
            retry_after,
        )
        return False, retry_after

    # Fixed-window counter. cache.add/cache.incr are atomic on Redis, which is
    # why production settings require REDIS_URL.
    window_id = now // window_seconds
    attempts_key = f"openkb_ai:attempts:{identifier}:{window_id}"
    if cache.add(attempts_key, 1, window_seconds + 5):
        attempts = 1
    else:
        try:
            attempts = cache.incr(attempts_key)
        except ValueError:
            cache.set(attempts_key, 1, window_seconds + 5)
            attempts = 1

    if attempts > max_requests:
        cache.set(block_key, now + block_seconds, block_seconds)
        logger.warning(
            "OpenKB AI rate limit exceeded: identifier=%s ip=%s user_id=%s attempts=%s window_seconds=%s block_seconds=%s",
            identifier,
            get_client_ip(request),
            request.user.pk if request.user.is_authenticated else "anonymous",
            attempts,
            window_seconds,
            block_seconds,
        )
        return False, block_seconds

    return True, 0


def clean_openkb_ai_answer(answer):
    """Hide internal OpenKB/source-path details before showing AI output."""
    cleaned = remove_openkb_internal_metadata(answer)

    internal_log_patterns = [
        r"(?i)last\s+operation\s+logged",
        r"(?i)operation\s+logged",
        r"(?i)wiki/log\.md",
        r"(?i)\blog\.md\b",
        r"(?i)openkb\s+log",
        r"(?i)previous\s+quer(?:y|ies)",
    ]
    if any(re.search(pattern, cleaned or "") for pattern in internal_log_patterns):
        return ""

    internal_detail_patterns = [
        r"(?i)\bindex\.md\b",
        r"(?i)\b[a-z0-9_\-/]+\.md\b",
        r"(?i)\bsources/",
        r"(?i)\bsummaries/",
        r"(?i)\bconcepts/",
        r"(?i)\bwiki/",
        r"(?i)\bfull_text\b",
        r"(?i)\bfrontmatter\b",
        r"(?i)\bread_file\b",
        r"(?i)\bget_page_content\b",
        r"(?i)\bget_image\b",
        r"(?i)i have read\b",
        r"(?i)\bi read\b",
        r"(?i)\bi checked\b",
        r"(?i)the document titles or descriptions",
        r"(?i)internal search",
        r"(?i)internal retrieval",
    ]

    # If the model leaks internal retrieval/file details while saying there is no
    # match, replace the whole answer with a safe user-facing message.
    if any(re.search(pattern, cleaned or "") for pattern in internal_detail_patterns):
        if answer_indicates_no_openkb_match(cleaned):
            return _("The knowledge base does not contain matching information about that topic.")

        cleaned = re.sub(r"`?index\.md`?", "the knowledge base", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"`?[a-z0-9_\-/]+\.md`?", "a knowledge-base article", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:sources|summaries|concepts|wiki)/[^\s`]+", "a knowledge-base source", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bfull_text\b", "article content", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bfrontmatter\b", "article metadata", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?i)\b(?:read_file|get_page_content|get_image)\b", "knowledge-base lookup", cleaned)

    # Remove internal source path bullets/lines that OpenKB may echo from summaries.
    cleaned = re.sub(
        r"(?im)^\s*[-*]?\s*(?:\*\*)?full\s+article\s+path(?:\*\*)?\s*:?\s*`?[^\n`]+`?\s*$",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?im)^\s*[-*]?\s*openkb\s+source\s*/\s*sources/[^\n]+$",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?im)^\s*[-*]?\s*source\s*:?\s*`?(?:sources|summaries|concepts|wiki)/[^\n`]+`?\s*$",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?im)^\s*[-*]?\s*`?(?:sources|summaries|concepts|wiki)/[^\n`]+\.md`?\s*$",
        "",
        cleaned,
    )

    # Last safety net: if internal markers still remain in a no-result answer,
    # do not display the original text to the user.
    if any(re.search(pattern, cleaned or "") for pattern in internal_detail_patterns) and answer_indicates_no_openkb_match(cleaned):
        return _("The knowledge base does not contain matching information about that topic.")

    # Collapse excess blank lines left by removed metadata.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned

def answer_indicates_no_openkb_match(answer):
    """Detect common no-result answers so we do not recommend random articles."""
    lowered = (answer or "").lower()
    no_match_phrases = [
        "couldn't find any information",
        "could not find any information",
        "i couldn't find",
        "i could not find",
        "cannot find any information",
        "cannot find information",
        "no information",
        "not find information",
        "not found in the wiki",
        "not in the wiki",
        "no relevant",
        "no matching",
        "nothing about",
        "does not contain matching information",
        "does not contain information",
        "there is no mention",
        "no mention of",
        "not contain matching",
        "cannot locate",
        "no article about",
        "no articles about",
        "no document about",
        "no documents about",
    ]
    return any(phrase in lowered for phrase in no_match_phrases)


def should_show_openkb_related_articles(question, answer, related_articles=None):
    """Show article recommendations when there are matching public articles."""
    if related_articles is not None:
        return bool(related_articles)

    if answer_indicates_no_openkb_match(answer):
        return False

    return is_openkb_article_recommendation_request(question)


def clean_openkb_ai_error_message(error):
    """Return a user-friendly AI error while detailed info remains in logs."""
    text = str(error or "")
    lowered = text.lower()

    if (
        "429" in lowered
        or "ratelimit" in lowered
        or "rate limit" in lowered
        or "quota" in lowered
        or "resource_exhausted" in lowered
        or "too many requests" in lowered
    ):
        return _("OpenKB AI is temporarily unavailable. Please try again later or contact IT support if the issue persists.")

    if "503" in lowered or "serviceunavailable" in lowered or "high demand" in lowered or "unavailable" in lowered:
        return _("OpenKB AI is temporarily unavailable. Please try again later or contact IT support if the issue persists.")

    if "timeout" in lowered:
        return _("OpenKB AI took too long to respond. Please try again later or contact IT support if the issue persists.")

    return _("OpenKB AI could not complete the request. Please try again later or contact IT support if the issue persists.")


def redact_openkb_debug_text(text, max_chars=2000):
    """Return debug text for logs without exposing common API-key formats."""
    value = str(text or "")[:max_chars]

    # Gemini API keys often begin with AIza. Keep only the prefix so logs prove
    # a key existed without leaking the full secret.
    value = re.sub(r"AIza[0-9A-Za-z_\-]{20,}", "AIza...REDACTED", value)

    # Generic Authorization/Bearer/key patterns seen in SDK/provider errors.
    value = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1REDACTED", value)
    value = re.sub(r"(?i)((?:api[_-]?key|key)\s*[:=]\s*)[^\s,;]+", r"\1REDACTED", value)

    return value


def openkb_ai_output_indicates_error(output):
    """Detect CLI error text that was printed as output instead of raised."""
    lowered = (output or "").lower().strip()
    if not lowered:
        return False
    error_markers = [
        "[error] query failed",
        "litellm.ratelimiterror",
        "litellm.serviceunavailableerror",
        "geminiexception",
        "resource_exhausted",
        "quota exceeded",
        "you exceeded your current quota",
    ]
    return any(marker in lowered for marker in error_markers)
