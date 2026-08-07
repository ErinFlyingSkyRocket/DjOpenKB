"""Helper functions split out from kb.views.services into kb.views.services_search.

This module is imported back by services.py so existing imports continue to work.
"""

from .services import *  # noqa: F401,F403
from django.db.models import Case, IntegerField, Value, When
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
        "type": str(_("Article")),
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
    """Return published title/keyword matches with keyword matches first.

    This deliberately uses only a lightweight database-side priority flag rather
    than a relevance-scoring loop or full-text search. Article bodies are not read.
    Within the keyword-priority and title-only groups, newer articles remain first.
    """
    query = (query or "").strip()[:120]
    query_words = tokenize_search_query(query)
    if not query and not query_words:
        return []

    full_query_filter = Q(title__icontains=query) | Q(keywords__icontains=query) if query else Q(pk__in=[])

    token_filter = Q()
    keyword_priority_filter = Q()
    if query:
        keyword_priority_filter |= Q(keywords__icontains=query)

    if query_words:
        for word in query_words:
            token_filter &= (Q(title__icontains=word) | Q(keywords__icontains=word))
            keyword_priority_filter |= Q(keywords__icontains=word)

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
            keyword_priority=Case(
                When(keyword_priority_filter, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            db_helpful_vote_count=Count(
                "votes",
                filter=Q(votes__value=ArticleVote.VoteValue.UP),
            ),
        )
        .order_by("keyword_priority", "-updated_at", "-pk")
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




def score_article_relationship(current_article, candidate_article):
    """Score relatedness using only article titles and explicit keywords."""
    current_keyword_phrases = {
        _normalize_article_search_text(value)
        for value in (current_article.get("keywords") or [])
        if _normalize_article_search_text(value)
    }
    candidate_keyword_phrases = {
        _normalize_article_search_text(value)
        for value in (candidate_article.get("keywords") or [])
        if _normalize_article_search_text(value)
    }
    current_keyword_tokens = set(tokenize_search_query(" ".join(current_keyword_phrases)))
    candidate_keyword_tokens = set(tokenize_search_query(" ".join(candidate_keyword_phrases)))
    current_title_tokens = set(tokenize_search_query(current_article.get("title") or ""))
    candidate_title_tokens = set(tokenize_search_query(candidate_article.get("title") or ""))

    score = 0
    score += len(current_keyword_phrases & candidate_keyword_phrases) * 120
    score += len(current_keyword_tokens & candidate_keyword_tokens) * 60
    score += len(current_keyword_tokens & candidate_title_tokens) * 35
    score += len(current_title_tokens & candidate_keyword_tokens) * 30
    score += len(current_title_tokens & candidate_title_tokens) * 20
    return score


def get_contextual_related_articles(current_article, limit=5, user=None):
    """Return related articles using a bounded title/keyword database query.

    Article bodies are never loaded or tokenised. PostgreSQL first narrows the
    candidates to title/keyword matches, then a small in-memory score chooses the
    best links. There is no random or popularity-only fallback.
    """
    if not current_article or limit <= 0:
        return []

    current_keywords = [
        str(value).strip()
        for value in (current_article.get("keywords") or [])
        if str(value).strip()
    ]
    current_title_tokens = tokenize_search_query(current_article.get("title") or "")
    search_terms = list(dict.fromkeys(current_keywords + current_title_tokens))[:30]
    if not search_terms:
        return []

    related_filter = None
    for term in search_terms:
        term_filter = Q(title__icontains=term) | Q(keywords__icontains=term)
        related_filter = term_filter if related_filter is None else related_filter | term_filter

    queryset = SuggestedArticle.objects.filter(
        status=SuggestedArticle.Status.PUBLISHED,
    ).filter(related_filter)

    if not user_can_view_internal_articles(user):
        queryset = queryset.filter(visibility=SuggestedArticle.Visibility.PUBLIC)

    current_id = current_article.get("suggested_id")
    if current_id:
        queryset = queryset.exclude(pk=current_id)

    candidate_limit = min(max(int(limit) * 10, 30), 100)
    queryset = queryset.only(
        "id",
        "title",
        "keywords",
        "visibility",
        "view_count",
        "updated_at",
    ).order_by("-view_count", "-updated_at", "-pk")[:candidate_limit]

    scored = []
    for article in queryset:
        candidate = {
            "title": article.title,
            "date": timezone.localtime(article.updated_at).strftime("%Y-%m-%d %H:%M"),
            "views": article.view_count or 0,
            "url": article.public_url,
            "path": "",
            "keywords": article.keyword_list,
            "suggested_id": article.pk,
            "visibility": article.visibility,
            "visibility_label": article.visibility_label,
        }
        score = score_article_relationship(current_article, candidate)
        if score <= 0:
            continue
        candidate["related_score"] = score
        scored.append(candidate)

    scored.sort(
        key=lambda item: (
            item.get("related_score") or 0,
            item.get("views") or 0,
            item.get("date") or "",
        ),
        reverse=True,
    )
    return scored[:limit]
