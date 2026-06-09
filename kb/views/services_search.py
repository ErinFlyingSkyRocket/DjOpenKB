"""Helper functions split out from kb.views.services into kb.views.services_search.

This module is imported back by services.py so existing imports continue to work.
"""

from .services import *  # noqa: F401,F403

def tokenize_search_query(value):
    """Return meaningful lowercase search tokens for ranking and related articles."""
    return [
        word
        for word in re.findall(r"[a-zA-Z0-9]+", (value or "").lower())
        if len(word) >= 2 and word not in SEARCH_STOPWORDS
    ]


def strip_markdown_for_search(markdown_text):
    """Create lightweight plain text from Markdown for snippets and ranking.

    Public search snippets should not expose internal Django/OpenKB metadata such
    as generated-by comments, article IDs, or generated keyword lines.
    """
    text = remove_openkb_internal_metadata(markdown_text)
    text = re.sub(r"^---.*?---", " ", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\*\*Keywords:\*\*\s*.*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#>*_~\-|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_search_excerpt(raw_markdown, query_words, max_length=180):
    """Return a short result snippet around the first useful query token."""
    plain_text = strip_markdown_for_search(raw_markdown)
    if not plain_text:
        return ""

    lower_text = plain_text.lower()
    first_match = -1
    for word in query_words:
        index = lower_text.find(word)
        if index >= 0 and (first_match == -1 or index < first_match):
            first_match = index

    if first_match < 0:
        return plain_text[:max_length].rstrip() + ("…" if len(plain_text) > max_length else "")

    start = max(0, first_match - 70)
    end = min(len(plain_text), start + max_length)
    snippet = plain_text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(plain_text):
        snippet = snippet + "…"
    return snippet


def score_article_for_query(article, query):
    """Score one public article for a user query. Higher means more relevant."""
    query = (query or "").strip().lower()
    query_words = tokenize_search_query(query)
    if not query and not query_words:
        return 0

    title = (article.get("title") or "").lower()
    raw_markdown = article.get("raw_markdown") or ""
    body = strip_markdown_for_search(raw_markdown).lower()
    keywords = " ".join(article.get("keywords") or []).lower()
    path = (article.get("path") or "").lower()
    author = (article.get("author") or "").lower()

    score = 0

    if query:
        if title == query:
            score += 150
        elif query in title:
            score += 90
        if query in keywords:
            score += 70
        if query in body:
            score += 35
        if query in path:
            score += 15

    matched_words = 0
    for word in query_words:
        word_score = 0
        if word in title:
            word_score += 20
        if word in keywords:
            word_score += 16
        if word in path:
            word_score += 7
        if word in author:
            word_score += 4

        body_hits = body.count(word)
        if body_hits:
            word_score += min(body_hits, 8) * 2

        if word_score:
            matched_words += 1
            score += word_score

    if query_words and matched_words == len(query_words):
        score += 30
    elif query_words and matched_words:
        score += matched_words * 3

    # Small tie-breakers: stronger source files, viewed articles, then recent files.
    if (article.get("type") or "").lower() == "openkb source":
        score += 5
    score += min(int(article.get("views") or 0), 50)

    return score


def rank_articles_for_query(articles, query):
    """Return matching articles sorted by relevance for the search page."""
    query_words = tokenize_search_query(query)
    ranked = []

    for article in articles:
        score = score_article_for_query(article, query)
        if score <= 0:
            continue

        item = dict(article)
        item["search_score"] = score
        item["search_excerpt"] = build_search_excerpt(item.get("raw_markdown", ""), query_words)
        ranked.append(item)

    ranked.sort(
        key=lambda item: (
            item.get("search_score") or 0,
            item.get("views") or 0,
            item.get("date") or "",
        ),
        reverse=True,
    )
    return ranked


def get_contextual_related_articles(current_article, limit=5):
    """Return article-page related links using title, keywords, body overlap, and views."""
    if not current_article:
        return []

    current_path = current_article.get("path") or ""
    current_keywords = " ".join(current_article.get("keywords") or [])
    current_title = current_article.get("title") or ""
    body_tokens = tokenize_search_query(strip_markdown_for_search(current_article.get("raw_markdown") or ""))
    unique_body_tokens = list(dict.fromkeys(body_tokens))[:30]
    related_query = " ".join([current_title, current_keywords, " ".join(unique_body_tokens)]).strip()

    candidates = [
        article
        for article in get_openkb_wiki_articles(sort_by_views=True)
        if article.get("path") != current_path
    ]

    scored = []
    for article in candidates:
        score = score_article_for_query(article, related_query)
        if score > 0:
            item = dict(article)
            item["related_score"] = score
            scored.append(item)

    scored.sort(
        key=lambda item: (
            item.get("related_score") or 0,
            item.get("views") or 0,
            item.get("date") or "",
        ),
        reverse=True,
    )

    # Fallback to trending/recent articles if the article has too little text to match.
    if len(scored) < limit:
        seen = {item.get("path") for item in scored}
        for article in candidates:
            if article.get("path") in seen:
                continue
            scored.append(article)
            seen.add(article.get("path"))
            if len(scored) >= limit:
                break

    return scored[:limit]


