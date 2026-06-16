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


def _normalize_article_search_text(value):
    """Normalize title/keyword values for simple contains checks."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def article_matches_title_or_keywords(article, query):
    """Return True only when the query appears in the article title or keywords.

    This intentionally does not inspect the article body, author, generated
    Markdown, OpenKB paths, or any internal content. The public search should be
    predictable: title and keyword fields only.
    """
    query = _normalize_article_search_text(query)
    query_words = tokenize_search_query(query)
    if not query and not query_words:
        return False

    title = _normalize_article_search_text(article.get("title"))
    keywords = _normalize_article_search_text(" ".join(article.get("keywords") or []))
    searchable_text = f"{title} {keywords}".strip()

    if query and query in searchable_text:
        return True

    if query_words:
        return all(word in searchable_text for word in query_words)

    return False


def build_search_article_card(suggested):
    """Build a lightweight public search result card without reading article body."""
    modified_at = timezone.localtime(suggested.updated_at).strftime("%Y-%m-%d %H:%M")
    keywords = suggested.keyword_list
    card = {
        "title": suggested.title,
        "type": "Article",
        "date": modified_at,
        "views": suggested.view_count or 0,
        "likes": getattr(suggested, "db_helpful_vote_count", 0) or 0,
        "url": suggested.public_url,
        "path": "",
        "raw_markdown": "",
        "author": suggested.author_display,
        "keywords": keywords,
        "suggested_id": suggested.pk,
        "visibility": suggested.visibility,
        "visibility_label": suggested.visibility_label,
    }
    card["search_excerpt"] = build_keyword_search_excerpt(card)
    return card


def search_public_articles_by_title_keywords(query, limit=None, visibility=None, user=None):
    """Return published articles matching title/keywords only, newest first.

    No relevance score is calculated and the article body is not read. The
    database filter only checks SuggestedArticle.title and SuggestedArticle.keywords.
    """
    query = (query or "").strip()[:120]
    query_words = tokenize_search_query(query)
    if not query and not query_words:
        return []

    full_query_filter = Q(title__icontains=query) | Q(keywords__icontains=query) if query else Q(pk__in=[])

    token_filter = Q()
    if query_words:
        for word in query_words:
            token_filter &= (Q(title__icontains=word) | Q(keywords__icontains=word))

    final_filter = full_query_filter | token_filter

    queryset = (
        SuggestedArticle.objects.select_related("owner")
        .filter(status=SuggestedArticle.Status.PUBLISHED)
        .filter(final_filter)
    )

    if visibility == "all":
        if not user_can_view_internal_articles(user):
            queryset = queryset.filter(visibility=SuggestedArticle.Visibility.PUBLIC)
    else:
        queryset = queryset.filter(visibility=normalize_article_visibility(visibility) if visibility else SuggestedArticle.Visibility.PUBLIC)

    queryset = (
        queryset
        .annotate(
            db_helpful_vote_count=Count(
                "votes",
                filter=Q(votes__value=ArticleVote.VoteValue.UP),
            )
        )
        .order_by("-updated_at", "-pk")
    )

    if limit is not None:
        queryset = queryset[:limit]

    return [build_search_article_card(suggested) for suggested in queryset]


def rank_articles_for_query(articles, query):
    """Backward-compatible wrapper for simple title/keyword filtering.

    Older views called this ranking helper. It now only filters by title and
    keywords, removes score-based ranking, and returns newest matches first.
    """
    matched = []
    for article in articles:
        if not article_matches_title_or_keywords(article, query):
            continue
        item = dict(article)
        item.pop("search_score", None)
        item["search_excerpt"] = build_keyword_search_excerpt(item)
        matched.append(item)

    matched.sort(key=lambda item: item.get("date") or "", reverse=True)
    return matched



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


def get_contextual_related_articles(current_article, limit=5, user=None):
    """Return genuinely related article-page links.

    Related articles are selected mainly by shared keywords. If there are no
    shared keywords, the function can still use meaningful title/body topic
    overlap, but it no longer falls back to random trending articles.
    """
    if not current_article:
        return []

    candidates = []
    for article in get_openkb_wiki_articles(sort_by_views=True, visibility="all", user=user):
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

