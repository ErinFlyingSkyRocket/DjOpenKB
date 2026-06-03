from .services import *
from django.utils.translation import gettext as _


@main_site_login_required
def suggest(request):
    init_openkb_storage()
    is_admin = user_is_site_admin(request.user)

    def render_suggest_form(extra_context=None):
        context = {
            "can_publish_directly": is_admin,
        }
        if extra_context:
            context.update(extra_context)
        return render(request, "suggest.html", context)

    if request.method == "GET":
        return render_suggest_form()

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()
    submit_action = request.POST.get("submit_action", "submit").strip()

    if submit_action == "draft":
        status = SuggestedArticle.Status.DRAFT
    elif is_admin:
        # Admin-created articles do not require approval. They are published
        # immediately when the admin uses the main submit button.
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

    timestamp_slug = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp_slug}-{slugify_title(title)}.md"

    article = SuggestedArticle.objects.create(
        owner=request.user,
        title=title,
        body=body,
        keywords=keywords_raw,
        filename=filename,
        wiki_path=f"sources/{filename}",
        raw_path=f"raw/{filename}",
        status=status,
        approved_by=request.user if status == SuggestedArticle.Status.PUBLISHED else None,
        approved_at=timezone.now() if status == SuggestedArticle.Status.PUBLISHED else None,
        image_assets=extract_article_image_filenames(body),
    )
    write_article_files(article)
    sync_article_image_assets(article, old_assets=[])
    clear_committed_pending_uploads(request, article.image_assets)

    if status == SuggestedArticle.Status.DRAFT:
        messages.success(request, _("Draft saved successfully."))
    elif status == SuggestedArticle.Status.PUBLISHED:
        messages.success(request, _("Article published successfully."))
    else:
        messages.success(request, _("Article submitted for admin approval."))
    return redirect("edit_my_suggestions")


@main_site_login_required
def edit_my_suggestions(request):
    search_query = request.GET.get("q", "").strip()

    article_queryset = SuggestedArticle.objects.filter(owner=request.user)
    total_user_article_count = article_queryset.count()

    if search_query:
        article_queryset = article_queryset.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(keywords__icontains=search_query)
            | Q(status__icontains=search_query)
            | Q(review_notes__icontains=search_query)
            | Q(review_notes_history__icontains=search_query)
            | Q(filename__icontains=search_query)
            | Q(wiki_path__icontains=search_query)
        )

    article_queryset = article_queryset.order_by("-updated_at", "-created_at")
    page_obj = paginate_articles(request, article_queryset, per_page=20)

    return render(request, "edit_my_suggestions.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "profile_search_query": search_query,
        "profile_result_count": article_queryset.count(),
        "total_user_article_count": total_user_article_count,
        "is_profile_search": bool(search_query),
        "profile_display_name": format_profile_display_name(request.user),
    })


@main_site_login_required
def edit_suggestion(request, article_id):
    article = get_object_or_404(SuggestedArticle, pk=article_id)

    if not user_can_manage_article(request.user, article):
        raise Http404("Article not found")

    return_url = get_safe_return_url(request, fallback_view_name="edit_my_suggestions")

    def render_edit_form(extra_context=None):
        context = {
            "article": article,
            "current_status": extra_context.get("current_status", article.status) if extra_context else article.status,
            "review_notes_value": article.review_notes,
            "review_notes_history": get_review_notes_history(article),
            "show_pending_failed_comments": article.status in {SuggestedArticle.Status.DRAFT, SuggestedArticle.Status.FAILED} and bool(article.review_notes),
            "existing_images_json": json.dumps(get_article_image_cards(article)),
            "return_url": return_url,
        }
        if extra_context:
            context.update(extra_context)
        return render(request, "suggest_edit.html", context)

    if request.method == "GET":
        return render_edit_form()

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()
    submit_action = request.POST.get("submit_action", "save").strip()

    previous_status = article.status

    if user_is_site_admin(request.user):
        status = request.POST.get("status", article.status).strip()
        if status not in SuggestedArticle.Status.values:
            status = article.status
    else:
        if article.status == SuggestedArticle.Status.PUBLISHED:
            # Once an article is approved, normal users cannot move it back to draft/pending.
            status = SuggestedArticle.Status.PUBLISHED
        elif submit_action == "draft":
            status = SuggestedArticle.Status.DRAFT
        else:
            # User publish/submit means pending admin approval, never direct public publishing.
            status = SuggestedArticle.Status.PENDING

    if user_is_site_admin(request.user):
        review_notes = (request.POST.get("review_notes") or "").strip()
    else:
        review_notes = article.review_notes

    error_context = {
        "title_value": title,
        "body_value": body,
        "keywords_value": keywords_raw,
        "status_value": status,
        "current_status": status,
        "review_notes_value": review_notes,
        "review_notes_history": get_review_notes_history(article),
        "existing_images_json": json.dumps(get_article_image_cards(article)),
        "return_url": return_url,
    }

    if user_is_site_admin(request.user) and status == SuggestedArticle.Status.FAILED and not review_notes:
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
    article.title = title
    article.body = body
    article.keywords = keywords_raw
    article.status = status
    article.image_assets = extract_article_image_filenames(body)

    if user_is_site_admin(request.user):
        if status == SuggestedArticle.Status.FAILED:
            if review_notes != article.review_notes or previous_status != SuggestedArticle.Status.FAILED:
                article.add_review_note_history(review_notes, reviewer=request.user, action="pending_failed")
            article.review_notes = review_notes
        elif status in {SuggestedArticle.Status.PENDING, SuggestedArticle.Status.PUBLISHED}:
            if article.review_notes:
                article.archive_current_review_note(actor=request.user, action=f"cleared_on_{status}")
            article.review_notes = ""
    elif status == SuggestedArticle.Status.PENDING and previous_status in {SuggestedArticle.Status.DRAFT, SuggestedArticle.Status.FAILED}:
        if article.review_notes:
            article.archive_current_review_note(actor=request.user, action="resubmitted")
        article.review_notes = ""

    if user_is_site_admin(request.user) and status == SuggestedArticle.Status.PUBLISHED and previous_status != SuggestedArticle.Status.PUBLISHED:
        article.approved_by = request.user
        article.approved_at = timezone.now()
    elif status != SuggestedArticle.Status.PUBLISHED:
        article.approved_by = None
        article.approved_at = None

    article.save()
    write_article_files(article)
    sync_article_image_assets(article, old_assets=old_image_assets)
    clear_committed_pending_uploads(request, article.image_assets)

    if status == SuggestedArticle.Status.DRAFT:
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

    if not user_can_manage_article(request.user, article):
        raise Http404("Article not found")

    return_url = get_safe_return_url(request, fallback_view_name="edit_my_suggestions")

    if request.method == "POST":
        title = article.title
        delete_article_files(article)
        article.delete()
        messages.success(request, f"Article deleted: {title}")
        return redirect(return_url)

    return render(request, "suggest_delete.html", {"article": article, "return_url": return_url})


@main_site_login_required
@require_POST
def upload_article_image(request):
    """Upload a small pasted image for use inside Markdown articles.

    The endpoint is intentionally login-protected because only logged-in users
    can create/edit suggestions. The returned Markdown can be inserted directly
    into the editor, for example: ![image](/wiki/uploads/abc.png)
    """
    uploaded_file = request.FILES.get("image")

    if not uploaded_file:
        return JsonResponse({"error": "No image file received."}, status=400)

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

    ArticleImageUploadLog.objects.create(
        filename=filename,
        original_name=(uploaded_file.name or "")[:255],
        content_type=(getattr(uploaded_file, "content_type", "") or "")[:100],
        size_bytes=getattr(uploaded_file, "size", 0) or 0,
        uploaded_by=request.user,
        upload_user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )

    pending_uploads = request.session.get("pending_article_uploads", [])
    if filename not in pending_uploads:
        pending_uploads.append(filename)
    request.session["pending_article_uploads"] = pending_uploads[-100:]
    request.session.modified = True

    image_url = f"/wiki/uploads/{filename}"
    return JsonResponse({
        "url": image_url,
        "filename": filename,
        "markdown": f"![image]({image_url})",
    })


@main_site_login_required
@require_POST
def delete_article_image(request):
    """Delete a pasted image that was uploaded during the current editing session.

    This endpoint is used by the editor's image preview tray. It only deletes
    filenames stored in the current session's pending upload list, so a user
    cannot delete arbitrary article images by guessing a filename.
    """
    filename = (request.POST.get("filename") or "").strip()
    if not filename:
        return JsonResponse({"error": "No image filename received."}, status=400)

    # Basic filename-only guard. Uploaded names are generated by the server and
    # should not contain path separators.
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        return JsonResponse({"error": "Invalid image filename."}, status=400)

    pending_uploads = request.session.get("pending_article_uploads", [])
    if filename not in pending_uploads and not user_is_site_admin(request.user):
        return JsonResponse({"error": "This image is not removable from this editing session."}, status=403)

    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / filename).resolve()
    if not str(file_path).startswith(str(upload_dir)):
        return JsonResponse({"error": "Invalid image path."}, status=400)

    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        mark_article_image_deleted(
            filename,
            actor=request.user,
            reason=ArticleImageUploadLog.DeleteReason.USER_REMOVED,
        )

    request.session["pending_article_uploads"] = [item for item in pending_uploads if item != filename]
    request.session.modified = True
    return JsonResponse({"deleted": True})


def serve_article_image(request, filename):
    """Serve article images safely.

    Published article images are public. Draft/pending/failed article images are
    only visible to the article owner or site admins, so admins can review
    pending submissions without exposing private draft uploads to everyone.
    Newly uploaded images that are not saved into an article yet are visible
    only in the uploader's current editing session.
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

    referenced_articles = SuggestedArticle.objects.filter(
        Q(image_assets__contains=[filename]) | Q(body__icontains=f"/wiki/uploads/{filename}")
    ).select_related("owner")

    has_reference = referenced_articles.exists()
    is_public_image = referenced_articles.filter(status=SuggestedArticle.Status.PUBLISHED).exists()

    if not is_public_image:
        allowed = False

        if request.user.is_authenticated:
            if user_is_site_admin(request.user):
                allowed = True
            else:
                allowed = referenced_articles.filter(owner=request.user).exists()

        if not allowed and not has_reference:
            pending_uploads = request.session.get("pending_article_uploads", [])
            allowed = filename in pending_uploads

        if not allowed:
            raise Http404("Image not found")

    return FileResponse(file_path.open("rb"), content_type=uploaded_image_content_type(filename))
