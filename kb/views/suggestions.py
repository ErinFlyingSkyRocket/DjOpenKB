from .services import *
from django.utils.translation import gettext as _
from urllib.parse import quote


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
            "is_admin_direct_publish": bool(is_admin and status == SuggestedArticle.Status.PUBLISHED),
            "image_count": len(article.image_assets or []),
        },
    )

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
            | Q(pending_update_title__icontains=search_query)
            | Q(pending_update_body__icontains=search_query)
            | Q(pending_update_keywords__icontains=search_query)
            | Q(update_status__icontains=search_query)
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
        extra_context = extra_context or {}
        pending_update_review = (
            user_is_site_admin(request.user)
            and article.status == SuggestedArticle.Status.PUBLISHED
            and article.update_status == SuggestedArticle.UpdateStatus.PENDING
        )
        use_pending_update_values = (
            article.status == SuggestedArticle.Status.PUBLISHED
            and article.update_status in {SuggestedArticle.UpdateStatus.PENDING, SuggestedArticle.UpdateStatus.FAILED}
            and bool(article.pending_update_body)
        )

        edit_title = article.pending_update_title if use_pending_update_values else article.title
        edit_body = article.pending_update_body if use_pending_update_values else article.body
        edit_keywords = article.pending_update_keywords if use_pending_update_values else article.keywords
        edit_image_assets = article.pending_update_image_assets if use_pending_update_values else article.image_assets

        back_url = return_url or reverse("edit_my_suggestions")

        context = {
            "article": article,
            "current_status": extra_context.get("current_status", article.status),
            "review_notes_value": article.review_notes,
            "review_notes_history": get_review_notes_history(article),
            "show_pending_failed_comments": article.status in {SuggestedArticle.Status.DRAFT, SuggestedArticle.Status.FAILED} and bool(article.review_notes),
            "existing_images_json": json.dumps(get_article_image_cards(article, image_assets=edit_image_assets)),
            "return_url": return_url,
            "back_url": back_url,
            "title_value": edit_title,
            "body_value": edit_body,
            "keywords_value": edit_keywords,
            "is_pending_update_review": pending_update_review,
            "has_pending_update": article.has_pending_update,
            "has_failed_update": article.has_failed_update,
            "has_update_draft": article.has_update_draft,
        }
        context.update(extra_context)
        return render(request, "suggest_edit.html", context)

    if request.method == "GET":
        return render_edit_form()

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()
    submit_action = request.POST.get("submit_action", "save").strip()

    previous_status = article.status
    previous_update_status = article.update_status

    is_admin_action = user_is_site_admin(request.user)
    is_published_update_flow = article.status == SuggestedArticle.Status.PUBLISHED and not is_admin_action
    is_admin_pending_update_review = (
        is_admin_action
        and article.status == SuggestedArticle.Status.PUBLISHED
        and article.update_status == SuggestedArticle.UpdateStatus.PENDING
    )

    if is_admin_action:
        status = request.POST.get("status", article.status).strip()
        admin_allowed_statuses = {
            SuggestedArticle.Status.PENDING,
            SuggestedArticle.Status.FAILED,
            SuggestedArticle.Status.PUBLISHED,
        }
        if status not in admin_allowed_statuses:
            # Admin review should not send articles back to Draft.
            # Keep the article in the review queue unless the admin explicitly
            # marks it as Pending failed or Published.
            status = SuggestedArticle.Status.PENDING
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

    if user_is_site_admin(request.user):
        review_notes = (request.POST.get("review_notes") or "").strip()
    else:
        review_notes = article.review_notes

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
        "return_url": return_url,
        "back_url": return_url or reverse("edit_my_suggestions"),
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
    write_public_files = True

    if is_published_update_flow:
        article.pending_update_title = title
        article.pending_update_body = body
        article.pending_update_keywords = keywords_raw
        article.pending_update_image_assets = new_image_assets
        write_public_files = False

        if submit_action == "save_update_draft":
            # Save the user's edited update progress without sending it back to
            # the admin review queue. This is mainly for rejected published
            # updates, where the author may need several editing sessions before
            # resubmitting. Keep the admin feedback visible by not clearing
            # review_notes and keep update_status as FAILED when applicable.
            if previous_update_status == SuggestedArticle.UpdateStatus.FAILED:
                article.update_status = SuggestedArticle.UpdateStatus.FAILED
            elif previous_update_status == SuggestedArticle.UpdateStatus.PENDING:
                article.update_status = SuggestedArticle.UpdateStatus.PENDING
            else:
                article.update_status = SuggestedArticle.UpdateStatus.FAILED
            if not article.update_reviewed_at and article.update_status == SuggestedArticle.UpdateStatus.FAILED:
                article.update_reviewed_at = timezone.now()
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
        else:
            # Keep pending update reviews constrained to approve or reject so the
            # already-published article is not accidentally hidden.
            return render_edit_form({
                **error_context,
                "error": _("Pending updates can only be approved as Published or marked as Pending failed."),
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
            "image_count": len(article.image_assets or []),
            "old_image_count": len(old_image_assets or []),
        },
    )

    if is_published_update_flow and submit_action == "save_update_draft":
        messages.success(request, _("Update progress saved. The published version is still visible, and the update has not been resubmitted for approval yet."))
    elif is_published_update_flow:
        messages.success(request, _("Article update submitted for admin approval. The published version is still visible until the update is approved."))
    elif is_admin_pending_update_review and status == SuggestedArticle.Status.PUBLISHED:
        messages.success(request, _("Article update approved and published."))
    elif is_admin_pending_update_review and status == SuggestedArticle.Status.FAILED:
        messages.success(request, _("Article update marked as pending failed. The current published version remains visible."))
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

    if not user_can_manage_article(request.user, article):
        raise Http404("Article not found")

    return_url = get_safe_return_url(request, fallback_view_name="edit_my_suggestions")

    if request.method == "POST":
        title = article.title
        article_id_for_log = article.pk
        article_status_for_log = article.status
        log_activity(
            request,
            ActivityLog.EventType.ARTICLE_DELETED,
            article=article,
            details={
                "action": "delete",
                "article_id": article_id_for_log,
                "title": title,
                "status": article_status_for_log,
                "is_admin_action": user_is_site_admin(request.user),
            },
        )
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
        Q(image_assets__contains=[filename])
        | Q(body__icontains=f"/wiki/uploads/{filename}")
        | Q(pending_update_image_assets__contains=[filename])
        | Q(pending_update_body__icontains=f"/wiki/uploads/{filename}")
    ).select_related("owner")

    has_reference = referenced_articles.exists()
    is_public_image = SuggestedArticle.objects.filter(
        status=SuggestedArticle.Status.PUBLISHED
    ).filter(
        Q(image_assets__contains=[filename]) | Q(body__icontains=f"/wiki/uploads/{filename}")
    ).exists()

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
