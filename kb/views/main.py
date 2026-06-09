from .services import *
from django.utils.translation import gettext as _


def home(request):
    all_articles = get_openkb_wiki_articles(sort_by_views=True)
    page_obj = paginate_articles(request, all_articles, per_page=10)

    return render(request, "index.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": page_obj.paginator,
        "total_article_count": len(all_articles),
    })


def article_detail(request, article_id):
    """Display an article through Django without exposing raw /wiki/*.md paths."""
    article = get_object_or_404(SuggestedArticle.objects.select_related("owner"), pk=article_id)

    if article.status != SuggestedArticle.Status.PUBLISHED and not user_can_manage_article(request.user, article):
        raise Http404("Article not found")

    record_article_session_view(request, article)

    raw_markdown = build_article_markdown(article)
    display_markdown = prepare_article_display_markdown(raw_markdown, article.title, article)
    html_content = render_safe_markdown(display_markdown)

    metadata = {
        "has_details": True,
        "type": "Article",
        "path": "",
        "published_at": article.approved_at or article.created_at,
        "updated_at": get_public_article_updated_at(article),
        "author": article.author_display,
        "author_username": article.author_username,
        "author_email": article.author_email,
        "author_account_type": article.author_account_type,
        "keywords": article.keyword_list,
        "permalink": request.build_absolute_uri(article.public_url),
        "view_count": article.view_count,
        "helpful_vote_count": article.votes.filter(value=ArticleVote.VoteValue.UP).count(),
        "unhelpful_vote_count": article.votes.filter(value=ArticleVote.VoteValue.DOWN).count(),
        "total_vote_count": article.votes.count(),
        "user_vote": (
            article.votes.filter(user=request.user).values_list("value", flat=True).first()
            if request.user.is_authenticated else None
        ),
        "vote_url": reverse("vote_article", kwargs={"article_id": article.pk}) if article.status == SuggestedArticle.Status.PUBLISHED else "",
        "can_vote": request.user.is_authenticated and article.status == SuggestedArticle.Status.PUBLISHED,
        "login_url": f'{reverse("login")}?next={request.get_full_path()}',
        "can_edit": request.user.is_authenticated and user_can_manage_article(request.user, article),
        "edit_url": reverse("edit_suggestion", kwargs={"article_id": article.pk}),
        "delete_url": reverse("delete_suggestion", kwargs={"article_id": article.pk}),
    }

    current_article_context = {
        "title": article.title,
        "path": "",
        "raw_markdown": raw_markdown,
        "keywords": article.keyword_list,
        "author": article.author_display,
    }
    featured_articles = get_contextual_related_articles(current_article_context, limit=5)

    return render(request, "articles.html", {
        "title": article.title,
        "content": html_content,
        "raw_markdown": raw_markdown,
        "metadata": metadata,
        "featured_articles": featured_articles,
        "can_use_admin_tools": user_can_use_admin_tools(request.user),
    })


def wiki_detail(request, wiki_path):
    """Block direct public access to raw OpenKB Markdown files.

    /wiki/uploads/<image> remains available through serve_article_image. For old
    article links under /wiki/sources/<file>.md, redirect to the safe Django
    article route. All other OpenKB internals such as index.md, log.md,
    summaries/, concepts/, and AGENTS.md return 404.
    """
    suggested = get_article_metadata_by_wiki_path(wiki_path)
    if suggested:
        return redirect(suggested.public_url)

    raise Http404("Wiki page not found")


@main_site_login_required
@require_POST
def vote_article(request, article_id):
    """Save one helpful/unhelpful vote per logged-in user per article."""
    article = get_object_or_404(
        SuggestedArticle,
        pk=article_id,
        status=SuggestedArticle.Status.PUBLISHED,
    )

    vote_value = request.POST.get("vote")
    if vote_value == "up":
        value = ArticleVote.VoteValue.UP
    elif vote_value == "down":
        value = ArticleVote.VoteValue.DOWN
    else:
        messages.error(request, _("Invalid vote."))
        return redirect(article.public_url)

    existing_vote = ArticleVote.objects.filter(
        article=article,
        user=request.user,
    ).first()

    if existing_vote and existing_vote.value == value:
        removed_value = existing_vote.value
        existing_vote.delete()
        log_activity(
            request,
            ActivityLog.EventType.VOTE_REMOVED,
            article=article,
            details={"removed_vote": "up" if removed_value == ArticleVote.VoteValue.UP else "down"},
        )
        messages.success(request, _("Your vote has been removed."))
    elif existing_vote:
        previous_value = existing_vote.value
        existing_vote.value = value
        existing_vote.save(update_fields=["value", "updated_at"])
        log_activity(
            request,
            ActivityLog.EventType.VOTE_UPDATED,
            article=article,
            details={
                "previous_vote": "up" if previous_value == ArticleVote.VoteValue.UP else "down",
                "new_vote": "up" if value == ArticleVote.VoteValue.UP else "down",
            },
        )
        messages.success(request, _("Your vote has been updated."))
    else:
        ArticleVote.objects.create(
            article=article,
            user=request.user,
            value=value,
        )
        log_activity(
            request,
            ActivityLog.EventType.VOTE_UP if value == ArticleVote.VoteValue.UP else ActivityLog.EventType.VOTE_DOWN,
            article=article,
            details={"vote": "up" if value == ArticleVote.VoteValue.UP else "down"},
        )
        messages.success(request, _("Thank you. Your vote has been saved."))

    next_url = request.POST.get("next") or article.public_url
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = article.public_url

    return redirect(next_url)



def search_article_suggestions(request):
    """Return a title-only list of matching published articles for the search dropdown."""
    init_openkb_storage()

    query = (request.GET.get("q") or "").strip()[:80]
    if len(query) < 2:
        return JsonResponse({"results": []})

    public_articles = get_openkb_wiki_articles(sort_by_views=False)
    ranked_articles = rank_articles_for_query(public_articles, query)[:8]

    results = []
    for article in ranked_articles:
        url = article.get("url") or "#"
        if not isinstance(url, str) or not url.startswith("/") or url.startswith("//"):
            url = "#"

        results.append({
            "title": article.get("title") or _("Untitled article"),
            "url": url,
        })

    return JsonResponse({"results": results})

def search_articles(request):
    """Search OpenKB articles with relevance ranking instead of plain substring order."""
    init_openkb_storage()

    query_original = request.GET.get("q", "").strip()
    all_public_articles = get_openkb_wiki_articles(sort_by_views=not bool(query_original))

    if query_original:
        all_articles = rank_articles_for_query(all_public_articles, query_original)
    else:
        all_articles = all_public_articles

    page_obj = paginate_articles(request, all_articles, per_page=20)

    return render(request, "index.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": page_obj.paginator,
        "search_query": query_original,
        "is_search": bool(query_original),
        "result_count": len(all_articles),
        "total_article_count": len(all_public_articles),
    })
