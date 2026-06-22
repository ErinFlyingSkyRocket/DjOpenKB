from .services import *
from collections import Counter
import json
import re
from django.utils.translation import gettext as _
from urllib.parse import quote


def _article_editor_review_mode(request):
    """Return True when the edit form is being used as a reviewer screen.

    This separates combined roles cleanly: Manage Pending -> Review uses the
    reviewer/status workflow, while My Articles -> Edit uses the author/edit
    workflow even if the same account also has approver rights.
    """
    value = (
        request.POST.get("editor_mode")
        or request.GET.get("editor_mode")
        or request.POST.get("review")
        or request.GET.get("review")
        or ""
    ).strip().lower()
    return value in {"review", "1", "true", "yes"}


def _normalise_keyword_suggestion(value):
    """Return a clean manually-entered keyword/tag value or an empty string if unsuitable."""
    value = re.sub(r"\s+", " ", (value or "").strip().lower())
    value = re.sub(r"[^a-z0-9+#.\- ]+", "", value).strip(" ,;:/\\")
    if not value or len(value) < 1 or len(value) > 80:
        return ""
    words = [word for word in value.split() if word]
    if not words or len(words) > 8:
        return ""
    return value


def _split_keywords_for_suggestions(value):
    candidates = []
    for item in re.split(r"[,;\n]+", value or ""):
        keyword = _normalise_keyword_suggestion(item)
        if keyword and keyword not in candidates:
            candidates.append(keyword)
    return candidates


def get_keyword_suggestion_catalog_json(visibility=SuggestedArticle.Visibility.PUBLIC, user=None):
    """Return existing manually-created keywords for the add/edit article forms.

    Suggestions are intentionally limited to keywords that already exist on
    published articles. No predetermined keyword list, filler-word filter,
    content scoring, or inferred keyword generation is used.
    """
    counts = Counter()

    visibility = normalize_article_visibility(visibility)
    queryset = (
        SuggestedArticle.objects
        .filter(status=SuggestedArticle.Status.PUBLISHED, visibility=visibility)
        .exclude(keywords="")
        .order_by("-updated_at")
        .values_list("keywords", flat=True)[:1000]
    )

    for keywords_raw in queryset:
        for keyword in _split_keywords_for_suggestions(keywords_raw):
            counts[keyword] += 1

    catalog = [
        {"keyword": keyword, "usage_count": count}
        for keyword, count in counts.items()
    ]
    catalog.sort(key=lambda item: (-item["usage_count"], item["keyword"]))
    return json.dumps(catalog[:500], ensure_ascii=False)



def _render_suggest_form_for_visibility(request, *, visibility, can_publish_directly, visibility_choices=None, extra_context=None):
    visibility = normalize_article_visibility(visibility)
    visibility_choices = visibility_choices or article_visibility_choices_for_user(request.user, action="add")
    show_visibility_selector = bool(
        len(visibility_choices) > 1
        and (user_can_add_internal_articles(request.user) or user_can_use_admin_tools(request.user))
    )
    context = {
        "can_publish_directly": can_publish_directly,
        "article_image_upload_limit": get_article_image_upload_limit(),
        "keyword_suggestion_catalog_json": get_keyword_suggestion_catalog_json(visibility=visibility, user=request.user),
        "article_visibility": visibility,
        "article_visibility_label": article_visibility_label(visibility),
        "visibility_choices": visibility_choices,
        "show_visibility_selector": show_visibility_selector,
        "is_internal_article_form": visibility == SuggestedArticle.Visibility.INTERNAL,
        "suggest_form_action": reverse("suggest"),
        "suggest_page_title": _("Add Article"),
        "suggest_submit_label": _("Submit article"),
    }
    if extra_context:
        context.update(extra_context)
    return render(request, "suggest.html", context)


def _suggest_unified(request):
    init_openkb_storage()
    can_publish_directly = user_can_use_admin_tools(request.user)
    visibility_choices = article_visibility_choices_for_user(request.user, action="add")
    if not visibility_choices:
        raise Http404("Article not found")

    if request.method == "POST":
        visibility = choose_requested_article_visibility(
            request.user,
            request.POST.get("article_visibility"),
            action="add",
            default=SuggestedArticle.Visibility.PUBLIC,
        )
    else:
        visibility = choose_requested_article_visibility(
            request.user,
            request.GET.get("visibility"),
            action="add",
            default=SuggestedArticle.Visibility.PUBLIC,
        )

    def render_suggest_form(extra_context=None):
        return _render_suggest_form_for_visibility(
            request,
            visibility=visibility,
            can_publish_directly=can_publish_directly,
            visibility_choices=visibility_choices,
            extra_context=extra_context,
        )

    if request.method == "GET":
        return render_suggest_form()

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()
    submit_action = request.POST.get("submit_action", "submit").strip()
    if submit_action not in {"draft", "submit"}:
        raise Http404("Article action not allowed")

    if submit_action == "draft":
        status = SuggestedArticle.Status.DRAFT
    elif can_publish_directly:
        # Admin Users can publish directly from the main submit button.
        status = SuggestedArticle.Status.PUBLISHED
    else:
        status = SuggestedArticle.Status.PENDING

    if len(title) < 5 or len(body) < 5:
        return render_suggest_form({
            "error": _("Article title and body must be at least 5 characters."),
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
        })

    duplicate_article = find_duplicate_article_by_title(title)
    if duplicate_article:
        return render_suggest_form({
            "error": duplicate_title_error_message(title),
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
        })

    new_image_assets = extract_article_image_filenames(body)
    try:
        new_image_assets = validate_article_image_count(new_image_assets)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_suggest_form({
            "error": message,
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": json.dumps(get_article_image_cards_from_filenames(new_image_assets)),
        })

    timestamp_slug = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp_slug}-{slugify_title(title)}.md"

    article = SuggestedArticle.objects.create(
        owner=request.user,
        title=title,
        body=body,
        keywords=keywords_raw,
        visibility=visibility,
        filename=filename,
        wiki_path=f"internal/sources/{filename}" if visibility == SuggestedArticle.Visibility.INTERNAL else f"sources/{filename}",
        raw_path=f"raw/internal/{filename}" if visibility == SuggestedArticle.Visibility.INTERNAL else f"raw/{filename}",
        status=status,
        approved_by=request.user if status == SuggestedArticle.Status.PUBLISHED else None,
        approved_at=timezone.now() if status == SuggestedArticle.Status.PUBLISHED else None,
        image_assets=new_image_assets,
    )
    write_article_files(article)
    sync_article_image_assets(article, old_assets=[])
    clear_committed_pending_uploads(request, article.image_assets)

    if status == SuggestedArticle.Status.DRAFT:
        activity_event = ActivityLog.EventType.ARTICLE_CREATED
    elif status == SuggestedArticle.Status.PUBLISHED:
        activity_event = ActivityLog.EventType.ARTICLE_APPROVED
    else:
        activity_event = ActivityLog.EventType.ARTICLE_SUBMITTED

    log_activity(
        request,
        activity_event,
        article=article,
        details={
            "action": "create",
            "status": status,
            "visibility": visibility,
            "is_admin_direct_publish": bool(can_publish_directly and status == SuggestedArticle.Status.PUBLISHED),
            "image_count": len(article.image_assets or []),
        },
    )

    if status == SuggestedArticle.Status.DRAFT:
        messages.success(request, _("Draft saved successfully."))
    elif status == SuggestedArticle.Status.PUBLISHED:
        messages.success(request, _("Article published successfully."))
    else:
        messages.success(request, _("Article submitted for admin approval."))

    redirect_url = reverse("edit_my_suggestions")
    # Keep My Articles role-scoped and combined by default. Users who can
    # author both public and internal articles should return to the combined
    # list after saving either type. Users with only internal authoring rights
    # still see only their internal article list.
    if visibility == SuggestedArticle.Visibility.INTERNAL and len(visibility_choices) == 1:
        redirect_url = f"{redirect_url}?visibility=internal"
    return redirect(redirect_url)


@main_site_login_required
def suggest(request):
    return _suggest_unified(request)


@main_site_login_required
def suggest_internal(request):
    # Backwards-compatible route for older internal links. The UI now uses the
    # normal Add Article page with a visibility selector when the user has both
    # public and internal authoring rights.
    if not user_can_add_internal_articles(request.user):
        raise Http404("Article not found")
    return redirect(f"{reverse('suggest')}?visibility=internal")


def _edit_my_suggestions_for_allowed_visibilities(request):
    # My Articles is the shared personal article workspace. Writers, Managers,
    # Internal Managers, and Admin Users use the same page, but it only lists
    # articles owned by the signed-in user. Broader manager/admin edits should
    # be done from the article detail/edit controls, not this personal list.
    allowed_visibilities = article_workspace_visibility_values_for_user(request.user)
    if not allowed_visibilities:
        raise Http404("Article not found")

    search_query = request.GET.get("q", "").strip()
    requested_visibility = (request.GET.get("visibility") or "all").strip().lower()
    if requested_visibility not in {"all", SuggestedArticle.Visibility.PUBLIC, SuggestedArticle.Visibility.INTERNAL}:
        requested_visibility = "all"
    if requested_visibility != "all" and requested_visibility not in allowed_visibilities:
        requested_visibility = "all"

    article_queryset = article_workspace_queryset_for_user(request.user)
    if requested_visibility != "all":
        article_queryset = article_queryset.filter(visibility=requested_visibility)

    total_user_article_count = article_queryset.count()

    if search_query:
        article_queryset = article_queryset.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(keywords__icontains=search_query)
            | Q(status__icontains=search_query)
            | Q(review_notes__icontains=search_query)
            | Q(review_notes_history__icontains=search_query)
            | Q(pending_update_title__icontains=search_query)
            | Q(pending_update_body__icontains=search_query)
            | Q(pending_update_keywords__icontains=search_query)
            | Q(update_status__icontains=search_query)
            | Q(filename__icontains=search_query)
            | Q(wiki_path__icontains=search_query)
        )

    article_queryset = article_queryset.order_by("-updated_at", "-created_at")
    page_obj = paginate_articles(request, article_queryset, per_page=20)
    for profile_article in page_obj.object_list:
        profile_article.delete_action_type = article_delete_action_type(request.user, profile_article)
        profile_article.can_open_edit = user_can_manage_article(request.user, profile_article, review_mode=False)

    new_article_url = reverse("suggest")
    if requested_visibility in {SuggestedArticle.Visibility.PUBLIC, SuggestedArticle.Visibility.INTERNAL}:
        new_article_url = f"{new_article_url}?visibility={requested_visibility}"
    elif len(allowed_visibilities) == 1:
        new_article_url = f"{new_article_url}?visibility={allowed_visibilities[0]}"

    filter_query_suffix = ""
    if requested_visibility != "all":
        filter_query_suffix += f"&visibility={requested_visibility}"
    if search_query:
        filter_query_suffix += f"&q={quote(search_query)}"

    return render(request, "edit_my_suggestions.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "profile_search_query": search_query,
        "profile_result_count": article_queryset.count(),
        "total_user_article_count": total_user_article_count,
        "is_profile_search": bool(search_query),
        "profile_display_name": format_profile_display_name(request.user),
        "article_visibility": requested_visibility,
        "profile_page_title": _("Manage my articles"),
        "profile_search_action": reverse("edit_my_suggestions"),
        "profile_new_article_url": new_article_url,
        "profile_visibility_filter": requested_visibility,
        "profile_allowed_public": SuggestedArticle.Visibility.PUBLIC in allowed_visibilities,
        "profile_allowed_internal": SuggestedArticle.Visibility.INTERNAL in allowed_visibilities,
        "profile_show_visibility_filter": len(allowed_visibilities) > 1,
        "profile_filter_query_suffix": filter_query_suffix,
        "profile_show_owner_column": False,
        "is_internal_space": False,
    })


@main_site_login_required
def edit_my_suggestions(request):
    return _edit_my_suggestions_for_allowed_visibilities(request)


@main_site_login_required
def edit_my_internal_suggestions(request):
    if not user_can_add_internal_articles(request.user):
        raise Http404("Article not found")

    allowed_visibilities = allowed_article_visibility_values_for_user(request.user, action="add")
    if len(allowed_visibilities) > 1:
        return redirect("edit_my_suggestions")
    return redirect(f"{reverse('edit_my_suggestions')}?visibility=internal")


@main_site_login_required
def edit_suggestion(request, article_id):
    article = get_object_or_404(SuggestedArticle, pk=article_id)
    is_review_mode = _article_editor_review_mode(request)

    if not user_can_manage_article(request.user, article, review_mode=is_review_mode):
        raise Http404("Article not found")

    # A forged review-mode flag should not turn a normal edit URL into a review
    # screen unless this user can really review the article in its visibility
    # scope and current workflow state.
    if is_review_mode and not user_can_review_article(request.user, article, review_mode=True):
        raise Http404("Article not found")

    fallback_view_name = "edit_my_internal_suggestions" if article.is_internal else "edit_my_suggestions"
    return_url = get_safe_return_url(request, fallback_view_name=fallback_view_name)

    def render_edit_form(extra_context=None):
        extra_context = extra_context or {}
        can_review_article = user_can_review_article(request.user, article, review_mode=is_review_mode)
        pending_update_review = (
            can_review_article
            and article.status == SuggestedArticle.Status.PUBLISHED
            and article.update_status == SuggestedArticle.UpdateStatus.PENDING
        )
        has_saved_update_draft = (
            article.status == SuggestedArticle.Status.PUBLISHED
            and article.update_status == SuggestedArticle.UpdateStatus.NONE
            and bool(article.pending_update_body)
        )
        use_pending_update_values = (
            article.status == SuggestedArticle.Status.PUBLISHED
            and bool(article.pending_update_body)
            and (
                article.update_status in {SuggestedArticle.UpdateStatus.PENDING, SuggestedArticle.UpdateStatus.FAILED}
                or has_saved_update_draft
            )
        )

        edit_title = article.pending_update_title if use_pending_update_values else article.title
        edit_body = article.pending_update_body if use_pending_update_values else article.body
        edit_keywords = article.pending_update_keywords if use_pending_update_values else article.keywords
        edit_image_assets = article.pending_update_image_assets if use_pending_update_values else article.image_assets

        back_url = return_url or reverse(fallback_view_name)
        can_edit_content = user_can_edit_article_content(request.user, article, review_mode=is_review_mode)
        can_change_visibility = user_can_change_article_visibility(request.user, article)
        visibility_choices = article_visibility_choices_for_user(request.user, action="add") if can_change_visibility else []
        allowed_statuses = allowed_article_statuses_for_admin_edit(article, user=request.user) if can_review_article else set()

        context = {
            "article": article,
            "current_status": extra_context.get("current_status", article.status),
            "review_notes_value": article.review_notes,
            "review_notes_history": get_review_notes_history(article),
            "show_pending_failed_comments": article.status in {SuggestedArticle.Status.DRAFT, SuggestedArticle.Status.FAILED} and bool(article.review_notes),
            "existing_images_json": json.dumps(get_article_image_cards(article, image_assets=edit_image_assets)),
            "article_image_upload_limit": get_article_image_upload_limit(),
            "keyword_suggestion_catalog_json": get_keyword_suggestion_catalog_json(visibility=article.visibility, user=request.user),
            "return_url": return_url,
            "back_url": back_url,
            "title_value": edit_title,
            "body_value": edit_body,
            "keywords_value": edit_keywords,
            "is_pending_update_review": pending_update_review,
            "can_review_article": can_review_article,
            "article_editor_mode": "review" if is_review_mode else "edit",
            "is_article_review_mode": is_review_mode,
            "can_edit_article_content": can_edit_content,
            "is_review_only_article": can_review_article and not can_edit_content,
            "can_change_article_visibility": can_change_visibility,
            "visibility_choices": visibility_choices,
            "can_use_admin_tools": user_can_use_admin_tools(request.user),
            "has_pending_update": article.has_pending_update,
            "has_failed_update": article.has_failed_update,
            "has_update_draft": article.has_update_draft,
            "has_saved_update_draft": has_saved_update_draft,
            "article_visibility": article.visibility,
            "article_visibility_label": article.visibility_label,
            "is_internal_article_form": article.is_internal,
            "allow_status_draft": SuggestedArticle.Status.DRAFT in allowed_statuses,
            "allow_status_pending": SuggestedArticle.Status.PENDING in allowed_statuses,
            "allow_status_failed": SuggestedArticle.Status.FAILED in allowed_statuses,
            "allow_status_published": SuggestedArticle.Status.PUBLISHED in allowed_statuses,
        }
        context.update(extra_context)
        return render(request, "suggest_edit.html", context)

    if request.method == "GET":
        return render_edit_form()

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()
    submit_action = validate_article_edit_action(
        request.user,
        article,
        request.POST.get("submit_action", "save"),
        review_mode=is_review_mode,
    )

    previous_status = article.status
    previous_update_status = article.update_status

    is_admin_action = user_can_review_article(request.user, article, review_mode=is_review_mode)
    is_published_update_flow = article.status == SuggestedArticle.Status.PUBLISHED and not is_admin_action
    is_admin_pending_update_review = (
        is_admin_action
        and article.status == SuggestedArticle.Status.PUBLISHED
        and article.update_status == SuggestedArticle.UpdateStatus.PENDING
    )

    if is_admin_action:
        status = validate_admin_requested_article_status(
            article,
            request.POST.get("status", article.status),
            user=request.user,
        )
    else:
        if article.status == SuggestedArticle.Status.PUBLISHED:
            # Published articles stay public. Normal user edits are stored as
            # pending_update_* fields until an admin approves them.
            status = SuggestedArticle.Status.PUBLISHED
        elif submit_action == "draft":
            status = SuggestedArticle.Status.DRAFT
        else:
            # User publish/submit means pending admin approval, never direct public publishing.
            status = SuggestedArticle.Status.PENDING

    can_edit_content = user_can_edit_article_content(request.user, article, review_mode=is_review_mode)

    if is_admin_action:
        review_notes = (request.POST.get("review_notes") or "").strip()
    else:
        review_notes = article.review_notes

    if not can_edit_content:
        if is_admin_pending_update_review:
            title = article.pending_update_title or article.title
            body = article.pending_update_body or article.body
            keywords_raw = article.pending_update_keywords or article.keywords
        else:
            title = article.title
            body = article.body
            keywords_raw = article.keywords

    requested_visibility = getattr(article, "visibility", SuggestedArticle.Visibility.PUBLIC)
    if user_can_change_article_visibility(request.user, article):
        requested_visibility = choose_requested_article_visibility(
            request.user,
            request.POST.get("article_visibility"),
            action="add",
            default=article.visibility,
        )

    if is_published_update_flow and submit_action == "revert_published":
        # Discard the user's pending/rejected update draft and return the edit
        # screen to the last approved public version. This does not modify the
        # published article files because article.title/body/keywords are already
        # the last approved content.
        if article.review_notes:
            article.archive_current_review_note(actor=request.user, action="update_reverted_to_published")
        article.review_notes = ""
        article.clear_pending_update()
        article.save()

        log_activity(
            request,
            ActivityLog.EventType.ARTICLE_UPDATED,
            article=article,
            details={
                "action": "revert_pending_update_to_published",
                "previous_status": previous_status,
                "previous_update_status": previous_update_status,
                "is_published_update_flow": True,
            },
        )
        messages.success(request, _("Editor reverted to the last published version."))
        return redirect(f"{reverse('edit_suggestion', kwargs={'article_id': article.pk})}?next={quote(return_url, safe='')}")

    error_context = {
        "title_value": title,
        "body_value": body,
        "keywords_value": keywords_raw,
        "status_value": status,
        "current_status": status,
        "review_notes_value": review_notes,
        "review_notes_history": get_review_notes_history(article),
        "existing_images_json": json.dumps(get_article_image_cards(article, image_assets=extract_article_image_filenames(body))),
        "article_image_upload_limit": get_article_image_upload_limit(),
        "return_url": return_url,
        "back_url": return_url or reverse(fallback_view_name),
        "article_visibility": requested_visibility,
    }

    if is_admin_action and status == SuggestedArticle.Status.FAILED and not review_notes:
        return render_edit_form({
            **error_context,
            "error": _("Please enter Pending failed comments before marking this article as Pending failed."),
        })

    if len(title) < 5 or len(body) < 5:
        return render_edit_form({
            **error_context,
            "review_notes_value": request.POST.get("review_notes", article.review_notes),
            "error": _("Article title and body must be at least 5 characters."),
        })

    duplicate_article = find_duplicate_article_by_title(title, exclude_pk=article.pk)
    if duplicate_article:
        return render_edit_form({
            **error_context,
            "error": duplicate_title_error_message(title),
        })

    old_image_assets = list(article.image_assets or extract_article_image_filenames(article.body))
    new_image_assets = extract_article_image_filenames(body)
    try:
        new_image_assets = validate_article_image_count(new_image_assets)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_edit_form({
            **error_context,
            "error": message,
        })

    visibility_changed = requested_visibility != getattr(article, "visibility", SuggestedArticle.Visibility.PUBLIC)
    if visibility_changed:
        if not user_can_change_article_visibility(request.user, article):
            raise Http404("Article visibility not allowed")
        delete_article_markdown_files(article)
        article.visibility = requested_visibility

    write_public_files = True

    if is_published_update_flow:
        article.pending_update_title = title
        article.pending_update_body = body
        article.pending_update_keywords = keywords_raw
        article.pending_update_image_assets = new_image_assets
        write_public_files = False

        if submit_action == "save_update_draft":
            # Save the user's edited update progress without changing the public
            # article and without forcing an immediate admin review.
            #
            # Existing rejected updates stay rejected so the admin feedback stays
            # visible. Existing pending updates stay pending because they are
            # already in the review queue. Brand-new published edits are saved
            # as a private update draft by keeping update_status as NONE while
            # storing the edited content in pending_update_* fields.
            if previous_update_status == SuggestedArticle.UpdateStatus.FAILED:
                article.update_status = SuggestedArticle.UpdateStatus.FAILED
                if not article.update_reviewed_at:
                    article.update_reviewed_at = timezone.now()
            elif previous_update_status == SuggestedArticle.UpdateStatus.PENDING:
                article.update_status = SuggestedArticle.UpdateStatus.PENDING
            else:
                article.update_status = SuggestedArticle.UpdateStatus.NONE
                article.update_submitted_at = None
                article.update_reviewed_at = None
        else:
            article.update_status = SuggestedArticle.UpdateStatus.PENDING
            article.update_submitted_at = timezone.now()
            article.update_reviewed_at = None
            # Do not archive the same rejection comment again when the author
            # resubmits an update. The existing admin rejection entry is already
            # stored in review_notes_history.
            article.review_notes = ""
    elif is_admin_pending_update_review:
        if status == SuggestedArticle.Status.PUBLISHED:
            article.title = title
            article.body = body
            article.keywords = keywords_raw
            article.image_assets = new_image_assets
            article.clear_pending_update()
            if article.review_notes:
                article.archive_current_review_note(actor=request.user, action="update_approved")
            article.review_notes = ""
            article.approved_by = request.user
            article.approved_at = timezone.now()
        elif status == SuggestedArticle.Status.FAILED:
            article.pending_update_title = title
            article.pending_update_body = body
            article.pending_update_keywords = keywords_raw
            article.pending_update_image_assets = new_image_assets
            article.update_status = SuggestedArticle.UpdateStatus.FAILED
            article.update_reviewed_at = timezone.now()
            if review_notes != article.review_notes or previous_update_status != SuggestedArticle.UpdateStatus.FAILED:
                article.add_review_note_history(review_notes, reviewer=request.user, action="update_pending_failed")
            article.review_notes = review_notes
            write_public_files = False
        elif status == SuggestedArticle.Status.PENDING:
            # Reviewer edited the submitted update but has not made the final
            # approve/reject decision yet. Keep the already-published version
            # visible and keep the update in the review queue.
            article.pending_update_title = title
            article.pending_update_body = body
            article.pending_update_keywords = keywords_raw
            article.pending_update_image_assets = new_image_assets
            article.update_status = SuggestedArticle.UpdateStatus.PENDING
            if not article.update_submitted_at:
                article.update_submitted_at = timezone.now()
            article.update_reviewed_at = None
            write_public_files = False
        else:
            # Keep pending update reviews constrained so the already-published
            # article is not accidentally hidden.
            return render_edit_form({
                **error_context,
                "error": _("Pending updates can only be kept pending, approved as Published, or marked as Pending failed."),
                "is_pending_update_review": True,
            })
    else:
        article.title = title
        article.body = body
        article.keywords = keywords_raw
        article.status = status
        article.image_assets = new_image_assets

        if is_admin_action:
            if status == SuggestedArticle.Status.FAILED:
                if review_notes != article.review_notes or previous_status != SuggestedArticle.Status.FAILED:
                    article.add_review_note_history(review_notes, reviewer=request.user, action="pending_failed")
                article.review_notes = review_notes
            elif status in {SuggestedArticle.Status.PENDING, SuggestedArticle.Status.PUBLISHED}:
                if article.review_notes:
                    article.archive_current_review_note(actor=request.user, action=f"cleared_on_{status}")
                article.review_notes = ""
        elif status == SuggestedArticle.Status.PENDING and previous_status in {SuggestedArticle.Status.DRAFT, SuggestedArticle.Status.FAILED}:
            # Do not archive the same rejection comment again when the author
            # resubmits. The actual admin rejection was already recorded.
            article.review_notes = ""

        if is_admin_action and status == SuggestedArticle.Status.PUBLISHED:
            # Admin save/publish changes the public version immediately, so refresh
            # the public approval timestamp even when the article was already published.
            article.approved_by = request.user
            article.approved_at = timezone.now()
        elif status != SuggestedArticle.Status.PUBLISHED:
            article.approved_by = None
            article.approved_at = None

    article.save()
    if write_public_files:
        write_article_files(article)
        sync_article_image_assets(article, old_assets=old_image_assets)
    clear_committed_pending_uploads(request, new_image_assets)

    effective_status = article.status

    if previous_status != effective_status:
        if effective_status == SuggestedArticle.Status.PUBLISHED:
            activity_event = ActivityLog.EventType.ARTICLE_APPROVED
        elif effective_status == SuggestedArticle.Status.FAILED:
            activity_event = ActivityLog.EventType.ARTICLE_REJECTED
        elif effective_status == SuggestedArticle.Status.PENDING:
            activity_event = ActivityLog.EventType.ARTICLE_SUBMITTED
        else:
            activity_event = ActivityLog.EventType.ARTICLE_STATUS_CHANGED
    else:
        activity_event = ActivityLog.EventType.ARTICLE_UPDATED

    log_activity(
        request,
        activity_event,
        article=article,
        details={
            "action": "edit",
            "previous_status": previous_status,
            "requested_status": status,
            "new_status": effective_status,
            "previous_update_status": previous_update_status,
            "is_admin_action": is_admin_action,
            "is_published_update_flow": is_published_update_flow,
            "is_admin_pending_update_review": is_admin_pending_update_review,
            "update_status": article.update_status,
            "visibility": article.visibility,
            "visibility_changed": visibility_changed,
            "can_edit_content": can_edit_content,
            "image_count": len(article.image_assets or []),
            "old_image_count": len(old_image_assets or []),
        },
    )

    if is_published_update_flow and submit_action == "save_update_draft":
        messages.success(request, _("Update progress saved. The published version is still visible, and the update has not been submitted for approval yet."))
        return redirect(f"{reverse('edit_suggestion', kwargs={'article_id': article.pk})}?next={quote(return_url, safe='')}")
    elif is_published_update_flow:
        messages.success(request, _("Article update submitted for admin approval. The published version is still visible until the update is approved."))
    elif is_admin_pending_update_review and status == SuggestedArticle.Status.PUBLISHED:
        messages.success(request, _("Article update approved and published."))
    elif is_admin_pending_update_review and status == SuggestedArticle.Status.FAILED:
        messages.success(request, _("Article update marked as pending failed. The current published version remains visible."))
    elif is_admin_pending_update_review and status == SuggestedArticle.Status.PENDING:
        messages.success(request, _("Review edits saved. The update remains pending approval and the current published version is still visible."))
    elif status == SuggestedArticle.Status.DRAFT:
        messages.success(request, _("Draft saved successfully."))
    elif status == SuggestedArticle.Status.PENDING:
        messages.success(request, _("Article submitted for admin approval."))
    elif status == SuggestedArticle.Status.FAILED:
        messages.success(request, _("Article marked as pending failed."))
    elif status == SuggestedArticle.Status.PUBLISHED and previous_status != SuggestedArticle.Status.PUBLISHED:
        messages.success(request, _("Article approved and published."))
    else:
        messages.success(request, _("Article updated successfully."))
    return redirect(return_url)


@main_site_login_required
def delete_suggestion(request, article_id):
    article = get_object_or_404(SuggestedArticle, pk=article_id)
    delete_action = article_delete_action_type(request.user, article)

    if delete_action == "none":
        raise Http404("Article not found")

    fallback_view_name = "edit_my_internal_suggestions" if article.is_internal else "edit_my_suggestions"
    return_url = get_safe_return_url(request, fallback_view_name=fallback_view_name)
    requires_mfa = article_requires_delete_mfa(article)

    if request.method == "POST":
        if requires_mfa and not verify_article_delete_mfa_code(request, article):
            return render(request, "suggest_delete.html", {
                "article": article,
                "return_url": return_url,
                "delete_action": delete_action,
                "requires_mfa": requires_mfa,
            })

        title = article.title
        # Close any historical pending deletion-request records from the older
        # approval workflow so stale requests do not remain in admin records.
        pending_requests = list(ArticleDeletionRequest.objects.filter(
            article=article,
            status=ArticleDeletionRequest.Status.PENDING,
        ))
        for deletion_request in pending_requests:
            deletion_request.status = ArticleDeletionRequest.Status.APPROVED
            deletion_request.reviewed_by = request.user
            deletion_request.reviewed_at = timezone.now()
            deletion_request.review_comment = deletion_request.review_comment or _("Article deletion confirmed after MFA.")
            deletion_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"])

        if article.status == SuggestedArticle.Status.PUBLISHED:
            if article_deletion_queue_immediate_delete_enabled():
                delete_article_immediately(
                    request,
                    article,
                    actor=request.user,
                    reason=_("Deleted from article page with immediate deletion setting."),
                    source="article_delete_immediate_published",
                    mfa_required=requires_mfa,
                    mfa_confirmed=requires_mfa,
                )
            else:
                queue_article_for_deletion(
                    request,
                    article,
                    actor=request.user,
                    reason=_("Deleted from article page."),
                    source="article_delete",
                    mfa_required=requires_mfa,
                    mfa_confirmed=requires_mfa,
                )
            messages.success(request, _("Article deletion confirmed: %(title)s.") % {"title": title})
        else:
            delete_article_immediately(
                request,
                article,
                actor=request.user,
                reason=_("Deleted from article page before publication."),
                source="article_delete",
                mfa_required=False,
                mfa_confirmed=False,
            )
            messages.success(request, _("Article permanently deleted: %(title)s.") % {"title": title})
        return redirect(return_url)

    return render(request, "suggest_delete.html", {
        "article": article,
        "return_url": return_url,
        "delete_action": delete_action,
        "requires_mfa": requires_mfa,
    })


@article_image_editor_required
@require_POST
def upload_article_image(request):
    """Upload a small pasted image for use inside Markdown articles.

    The endpoint is intentionally login-protected because only logged-in users
    can create/edit suggestions. The returned Markdown can be inserted directly
    into the editor, for example: ![image](/wiki/uploads/abc.png)
    """
    uploaded_file = request.FILES.get("image")

    if not uploaded_file:
        return JsonResponse({"error": _("No image file received.")}, status=400)

    upload_limit = get_article_image_upload_limit()
    pending_uploads = request.session.get("pending_article_uploads", [])
    active_pending_uploads = list(dict.fromkeys(safe_uploaded_filename(item) for item in pending_uploads if safe_uploaded_filename(item)))
    if upload_limit <= 0:
        return JsonResponse({"error": article_image_limit_error_message(limit=upload_limit)}, status=403)
    if len(active_pending_uploads) >= upload_limit:
        return JsonResponse({"error": article_image_limit_error_message(limit=upload_limit)}, status=400)

    try:
        image_info = validate_article_image_upload(uploaded_file)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return JsonResponse({"error": message}, status=400)

    extension = image_info["extension"]

    upload_dir = get_openkb_uploads_dir()
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{uuid.uuid4().hex[:12]}{extension}"
    file_path = upload_dir / filename

    with file_path.open("wb") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)

    image_log = ArticleImageUploadLog.objects.create(
        filename=filename,
        original_name=(uploaded_file.name or "")[:255],
        content_type=(getattr(uploaded_file, "content_type", "") or "")[:100],
        size_bytes=getattr(uploaded_file, "size", 0) or 0,
        uploaded_by=request.user,
        upload_ip_address=get_client_ip(request),
        upload_user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    log_activity(
        request,
        ActivityLog.EventType.IMAGE_UPLOADED,
        details={
            "filename": filename,
            "original_name": image_log.original_name,
            "content_type": image_log.content_type,
            "size_bytes": image_log.size_bytes,
        },
    )

    pending_uploads = active_pending_uploads
    if filename not in pending_uploads:
        pending_uploads.append(filename)
    request.session["pending_article_uploads"] = pending_uploads[-upload_limit:]
    request.session.modified = True

    image_url = f"/wiki/uploads/{filename}"
    return JsonResponse({
        "url": image_url,
        "filename": filename,
        "markdown": f"![image]({image_url})",
    })


@article_image_editor_required
@require_POST
def delete_article_image(request):
    """Delete a pasted image that was uploaded during the current editing session.

    This endpoint is used by the editor's image preview tray. It only deletes
    filenames stored in the current session's pending upload list, so a user
    cannot delete arbitrary article images by guessing a filename.
    """
    filename = (request.POST.get("filename") or "").strip()
    if not filename:
        return JsonResponse({"error": _("No image filename received.")}, status=400)

    # Basic filename-only guard. Uploaded names are generated by the server and
    # should not contain path separators.
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        return JsonResponse({"error": _("Invalid image filename.")}, status=400)

    pending_uploads = request.session.get("pending_article_uploads", [])
    if filename not in pending_uploads:
        return JsonResponse({"error": _("This image is not removable from this editing session.")}, status=403)

    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / filename).resolve()
    try:
        file_path.relative_to(upload_dir)
    except ValueError:
        return JsonResponse({"error": _("Invalid image path.")}, status=400)

    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        mark_article_image_deleted(
            filename,
            actor=request.user,
            reason=ArticleImageUploadLog.DeleteReason.USER_REMOVED,
            record_activity=False,
        )
        log_activity(
            request,
            ActivityLog.EventType.IMAGE_DELETED,
            details={
                "filename": filename,
                "reason": ArticleImageUploadLog.DeleteReason.USER_REMOVED,
                "source": "editor_preview",
            },
        )

    request.session["pending_article_uploads"] = [item for item in pending_uploads if item != filename]
    request.session.modified = True
    return JsonResponse({"deleted": True})


@main_site_login_required
def serve_article_image(request, filename):
    """Serve article images with the same public/internal visibility rules as articles.

    Published public article images are visible to normal signed-in users.
    Published internal article images require internal article view permission.
    Draft/pending/failed article images remain limited to the article owner or
    users who can review/manage that article's visibility scope.
    """
    if not is_allowed_article_image_filename(filename):
        raise Http404("Image not found")

    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / filename).resolve()

    try:
        file_path.relative_to(upload_dir)
    except ValueError:
        raise Http404("Invalid image path")

    if not file_path.exists() or not file_path.is_file():
        raise Http404("Image not found")

    referenced_articles = (
        SuggestedArticle.objects
        .filter(
            Q(image_assets__contains=[filename])
            | Q(body__icontains=f"/wiki/uploads/{filename}")
            | Q(pending_update_image_assets__contains=[filename])
            | Q(pending_update_body__icontains=f"/wiki/uploads/{filename}")
        )
        .exclude(status=SuggestedArticle.Status.DELETE_QUEUED)
        .select_related("owner")
    )

    has_reference = referenced_articles.exists()

    published_references = referenced_articles.filter(status=SuggestedArticle.Status.PUBLISHED)
    has_public_published_reference = published_references.filter(
        visibility=SuggestedArticle.Visibility.PUBLIC
    ).exists()
    has_internal_published_reference = published_references.filter(
        visibility=SuggestedArticle.Visibility.INTERNAL
    ).exists()

    if has_internal_published_reference and not user_can_view_internal_articles(request.user):
        # Internal references are treated as private even if the same file is
        # accidentally embedded elsewhere.
        raise Http404("Image not found")

    if has_public_published_reference or has_internal_published_reference:
        if not any(user_can_view_article(request.user, article) for article in published_references):
            raise Http404("Image not found")
        return FileResponse(file_path.open("rb"), content_type=uploaded_image_content_type(filename))

    allowed = False

    if request.user.is_authenticated:
        for article in referenced_articles:
            if article.owner_id == request.user.id or user_can_review_article(request.user, article, review_mode=True) or user_can_delete_article(request.user, article):
                allowed = True
                break

    if not allowed and not has_reference:
        pending_uploads = request.session.get("pending_article_uploads", [])
        allowed = filename in pending_uploads

    if not allowed:
        raise Http404("Image not found")

    return FileResponse(file_path.open("rb"), content_type=uploaded_image_content_type(filename))
