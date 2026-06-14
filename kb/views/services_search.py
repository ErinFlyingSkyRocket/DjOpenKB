"""Helper functions split out from kb.views.services into kb.views.services_search.

This module is imported back by services.py so existing imports continue to work.
"""

from .services import *  # noqa: F401,F403
from django.utils.translation import gettext as _

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


def build_keyword_search_excerpt(article, max_keywords=8):
    """Return a short search helper line using article keywords only."""
    keywords = [keyword for keyword in (article.get("keywords") or []) if keyword]
    if not keywords:
        return ""

    keyword_text = ", ".join(keywords[:max_keywords])
    if len(keywords) > max_keywords:
        keyword_text += "…"
    return _("Keywords: %(keywords)s") % {"keywords": keyword_text}


def score_article_for_query(article, query):
    """Score one public article using only the title and article keywords.

    The main site search is intentionally simple: it does not scan the full
    Markdown body, author name, or internal file path. This keeps results closer
    to what users expect when they search by article topic or known title.
    """
    query = (query or "").strip().lower()
    query_words = tokenize_search_query(query)
    if not query and not query_words:
        return 0

    title = (article.get("title") or "").lower()
    keywords = " ".join(article.get("keywords") or []).lower()

    score = 0

    if query:
        if title == query:
            score += 200
        elif title.startswith(query):
            score += 140
        elif query in title:
            score += 100

        if keywords == query:
            score += 120
        elif query in keywords:
            score += 80

    matched_words = 0
    for word in query_words:
        word_score = 0
        if word in title:
            word_score += 35
        if word in keywords:
            word_score += 28

        if word_score:
            matched_words += 1
            score += word_score

    if query_words and matched_words == len(query_words):
        score += 40
    elif query_words and matched_words:
        score += matched_words * 5

    # Tie-breakers only. Search relevance still comes from title/keywords.
    score += min(int(article.get("likes") or 0), 30)
    score += min(int(article.get("views") or 0), 30)

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
        item["search_excerpt"] = build_keyword_search_excerpt(item)
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


def _article_identity_matches(left, right):
    """Return True when two article dictionaries refer to the same Django article."""
    left_id = left.get("suggested_id")
    right_id = right.get("suggested_id")
    if left_id and right_id and str(left_id) == str(right_id):
        return True

    left_url = left.get("url")
    right_url = right.get("url")
    if left_url and right_url and left_url == right_url:
        return True

    left_path = left.get("path")
    right_path = right.get("path")
    if left_path and right_path and left_path == right_path:
        return True

    return False


def score_article_relationship(current_article, candidate_article):
    """Score how related a candidate is to the current article.

    The article page sidebar should show genuinely related articles, not random
    recommendations. Keyword overlap is weighted highest, followed by title/body
    topic overlap. View count is used only as a very small tie-breaker.
    """
    current_keywords = set(tokenize_search_query(" ".join(current_article.get("keywords") or [])))
    candidate_keywords = set(tokenize_search_query(" ".join(candidate_article.get("keywords") or [])))

    current_title_tokens = set(tokenize_search_query(current_article.get("title") or ""))
    candidate_title_tokens = set(tokenize_search_query(candidate_article.get("title") or ""))

    current_body_tokens = set(tokenize_search_query(strip_markdown_for_search(current_article.get("raw_markdown") or "")))
    candidate_body_tokens = set(tokenize_search_query(strip_markdown_for_search(candidate_article.get("raw_markdown") or "")))

    keyword_overlap = current_keywords & candidate_keywords
    title_overlap = current_title_tokens & candidate_title_tokens
    topic_overlap = (current_keywords | current_title_tokens | current_body_tokens) & (
        candidate_keywords | candidate_title_tokens | candidate_body_tokens
    )

    score = 0
    score += len(keyword_overlap) * 100
    score += len(current_keywords & candidate_title_tokens) * 45
    score += len(current_title_tokens & candidate_keywords) * 35
    score += len(title_overlap) * 25
    score += min(len(topic_overlap), 12) * 8

    # Small tie-breaker only. This should never make an unrelated article appear.
    score += min(int(candidate_article.get("views") or 0), 20)

    return score


def get_contextual_related_articles(current_article, limit=5):
    """Return genuinely related article-page links.

    Related articles are selected mainly by shared keywords. If there are no
    shared keywords, the function can still use meaningful title/body topic
    overlap, but it no longer falls back to random trending articles.
    """
    if not current_article:
        return []

    candidates = []
    for article in get_openkb_wiki_articles(sort_by_views=True):
        if _article_identity_matches(current_article, article):
            continue
        candidates.append(article)

    scored = []
    for article in candidates:
        score = score_article_relationship(current_article, article)
        if score >= 40:
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

    return scored[:limit]

