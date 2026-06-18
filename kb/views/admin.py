import logging
from .services import *
from django.contrib.auth.models import User
from django.db.models import Q
from django.db import transaction
from django.utils.translation import gettext as _
from urllib.parse import quote

logger = logging.getLogger(__name__)



@admin_tools_required
def clean_stray_upload_files(request):
    # Manual admin cleanup should show/delete stray files immediately.
    # The configured SiteSetting threshold is kept for the automatic cleanup command/scheduler.
    min_age_minutes = 0
    automatic_min_age_minutes = get_stray_upload_cleanup_min_age_minutes()
    stray_files = find_stray_uploaded_files(min_age_minutes=min_age_minutes)
    total_size_bytes = sum(item["size_bytes"] for item in stray_files)

    if request.method == "POST":
        selected_filenames = {
            safe_uploaded_filename(filename)
            for filename in request.POST.getlist("selected_files")
        }
        selected_filenames.discard("")

        if not selected_filenames:
            messages.warning(request, _("No stray upload files were selected for deletion."))
            return redirect("clean_stray_upload_files")

        deleted_count = 0
        deleted_size_bytes = 0
        skipped_count = 0
        errors = []

        # Re-scan on POST so the cleanup uses the latest file/article state.
        # Only delete files the admin explicitly selected with the checkboxes.
        for item in stray_files:
            if item["filename"] not in selected_filenames:
                continue

            file_path = item["path"]
            upload_dir = get_openkb_uploads_dir().resolve()
            file_path = file_path.resolve()

            try:
                file_path.relative_to(upload_dir)
            except ValueError:
                errors.append(_("Skipped invalid path: %(filename)s") % {"filename": item["filename"]})
                skipped_count += 1
                continue

            try:
                if file_path.exists() and file_path.is_file():
                    deleted_size_bytes += file_path.stat().st_size
                    file_path.unlink()
                    mark_article_image_deleted(
                        item["filename"],
                        actor=request.user,
                        reason=ArticleImageUploadLog.DeleteReason.ADMIN_CLEANUP,
                    )
                    deleted_count += 1
                else:
                    skipped_count += 1
            except OSError as error:
                errors.append(f"Could not delete {item['filename']}: {error}")
                skipped_count += 1

        missing_count = max(len(selected_filenames) - deleted_count - skipped_count - len(errors), 0)

        if deleted_count:
            messages.success(
                request,
                f"Cleaned up {deleted_count} selected stray upload file(s), freeing {round(deleted_size_bytes / 1024, 1)} KB."
            )
        else:
            messages.info(request, "No selected stray upload files were deleted.")

        if skipped_count or missing_count:
            messages.info(request, "Some selected files were skipped because they were no longer available or no longer matched the stray file scan.")

        for error in errors[:5]:
            messages.error(request, error)

        return redirect("clean_stray_upload_files")

    return render(request, "admin_clean_stray_upload_files.html", {
        "stray_files": stray_files,
        "stray_count": len(stray_files),
        "total_size_kb": round(total_size_bytes / 1024, 1),
        "min_age_minutes": min_age_minutes,
        "automatic_min_age_minutes": automatic_min_age_minutes,
    })


def _set_bulk_import_result(request, *, status, title, filename="", size_bytes=0, imported_count=0, owner="", errors=None):
    """Store the latest import result so the redirected page can show it in a modal."""
    error_list = [str(error) for error in (errors or [])]
    request.session["bulk_import_result"] = {
        "status": status,
        "title": str(title),
        "filename": str(filename or ""),
        "size_bytes": int(size_bytes or 0),
        "imported_count": int(imported_count or 0),
        "owner": str(owner or ""),
        "error_count": len(error_list),
        "errors": error_list,
    }


@admin_tools_required
def admin_bulk_articles(request):
    """Admin page for importing/exporting article bundles."""
    import_result = request.session.pop("bulk_import_result", None)
    return render(request, "admin_bulk_articles.html", {
        "import_result": import_result,
    })


@admin_tools_required
def export_articles_zip(request):
    """Export Django-managed articles and referenced uploads as one zip or split parts."""
    force_split = request.GET.get("split") == "1"
    data, filename, content_type, manifest, was_split = build_bulk_export_download(force_split=force_split)

    if was_split:
        article_count = sum(part.get("article_count", 0) for part in manifest.get("parts", []))
        upload_count = sum(part.get("upload_count", 0) for part in manifest.get("parts", []))
        part_count = manifest.get("part_count", 0)
    else:
        article_count = len(manifest.get("articles", []))
        upload_count = len(manifest.get("uploads", []))
        part_count = 1

    log_activity(
        request,
        ActivityLog.EventType.ADMIN_TOOL_ACTION,
        details={
            "tool": "bulk_export_articles",
            "article_count": article_count,
            "upload_count": upload_count,
            "part_count": part_count,
            "split_export": was_split,
        },
    )

    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin_tools_required
@require_POST
def import_articles_zip(request):
    """Import articles from a zip and assign ownership to the current admin user."""
    if request.method != "POST":
        return redirect("admin_bulk_articles")

    uploaded_zip = request.FILES.get("import_zip")
    owner_name = request.user.get_username()

    if not uploaded_zip:
        _set_bulk_import_result(
            request,
            status="danger",
            title=_("Import failed"),
            errors=[_("Please choose a .zip file to import.")],
            owner=owner_name,
        )
        return redirect("admin_bulk_articles")

    if not uploaded_zip.name.lower().endswith(".zip"):
        _set_bulk_import_result(
            request,
            status="danger",
            title=_("Import failed"),
            filename=uploaded_zip.name,
            size_bytes=uploaded_zip.size,
            owner=owner_name,
            errors=[_("Only .zip import files are allowed.")],
        )
        return redirect("admin_bulk_articles")

    if uploaded_zip.size > BULK_IMPORT_MAX_UPLOAD_BYTES:
        _set_bulk_import_result(
            request,
            status="danger",
            title=_("Import failed"),
            filename=uploaded_zip.name,
            size_bytes=uploaded_zip.size,
            owner=owner_name,
            errors=[_("Import zip is too large. Maximum allowed size is 100 MB. For split exports, extract the split package and import each part zip one at a time.")],
        )
        return redirect("admin_bulk_articles")

    try:
        imported_count, errors = import_articles_from_zip(uploaded_zip, owner=request.user)
    except zipfile.BadZipFile:
        _set_bulk_import_result(
            request,
            status="danger",
            title=_("Import failed"),
            filename=uploaded_zip.name,
            size_bytes=uploaded_zip.size,
            owner=owner_name,
            errors=[_("Invalid zip file.")],
        )
        return redirect("admin_bulk_articles")
    except ValueError as error:
        _set_bulk_import_result(
            request,
            status="danger",
            title=_("Import failed"),
            filename=uploaded_zip.name,
            size_bytes=uploaded_zip.size,
            owner=owner_name,
            errors=[str(error)],
        )
        return redirect("admin_bulk_articles")
    except Exception as error:
        _set_bulk_import_result(
            request,
            status="danger",
            title=_("Import failed"),
            filename=uploaded_zip.name,
            size_bytes=uploaded_zip.size,
            owner=owner_name,
            errors=[_("Import failed: %(error)s") % {"error": error}],
        )
        return redirect("admin_bulk_articles")

    if imported_count and errors:
        result_status = "warning"
        result_title = _("Import completed with warnings")
    elif imported_count:
        result_status = "success"
        result_title = _("Import completed")
    else:
        result_status = "warning"
        result_title = _("No articles were imported")

    _set_bulk_import_result(
        request,
        status=result_status,
        title=result_title,
        filename=uploaded_zip.name,
        size_bytes=uploaded_zip.size,
        imported_count=imported_count,
        owner=owner_name,
        errors=errors,
    )

    log_activity(
        request,
        ActivityLog.EventType.BULK_IMPORT,
        details={
            "filename": uploaded_zip.name,
            "size_bytes": uploaded_zip.size,
            "imported_count": imported_count,
            "error_count": len(errors),
            "owner": owner_name,
        },
    )

    return redirect("admin_bulk_articles")



def _manage_pending_articles_for_allowed_visibilities(request):
    allowed_visibilities = allowed_article_visibility_values_for_user(request.user, action="manage")
    if not allowed_visibilities:
        raise Http404("Article not found")

    search_query = request.GET.get("q", "").strip()

    # Users with only one management scope are locked to that scope.
    # Users who can manage both public and internal queues see all allowed
    # pending reviews by default, with an optional dropdown to narrow the view.
    if len(allowed_visibilities) == 1:
        requested_visibility = allowed_visibilities[0]
    else:
        requested_visibility = (request.GET.get("visibility") or "all").strip().lower()
        if requested_visibility != "all" and requested_visibility not in allowed_visibilities:
            requested_visibility = "all"

    if request.method == "POST":
        # Deletion approval requests were replaced by direct MFA-confirmed
        # deletion. The manage-pending screen is now for reviewing article
        # submissions and pending updates only.
        raise Http404("Article action not allowed")

    base_pending_queryset = SuggestedArticle.objects.select_related("owner").filter(
        Q(status=SuggestedArticle.Status.PENDING)
        | Q(update_status=SuggestedArticle.UpdateStatus.PENDING),
        visibility__in=allowed_visibilities,
    )

    if requested_visibility == "all":
        article_queryset = base_pending_queryset
    else:
        article_queryset = base_pending_queryset.filter(visibility=requested_visibility)

    if search_query:
        article_queryset = article_queryset.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(keywords__icontains=search_query)
            | Q(review_notes__icontains=search_query)
            | Q(review_notes_history__icontains=search_query)
            | Q(pending_update_title__icontains=search_query)
            | Q(pending_update_body__icontains=search_query)
            | Q(pending_update_keywords__icontains=search_query)
            | Q(update_status__icontains=search_query)
            | Q(filename__icontains=search_query)
            | Q(owner__username__icontains=search_query)
            | Q(owner__email__icontains=search_query)
            | Q(author_username_snapshot__icontains=search_query)
            | Q(author_email_snapshot__icontains=search_query)
        )
    article_queryset = article_queryset.order_by("created_at", "updated_at")
    total_article_review_count = article_queryset.count()
    total_pending_article_count = total_article_review_count
    public_pending_count = (
        base_pending_queryset.filter(visibility=SuggestedArticle.Visibility.PUBLIC).count()
        if SuggestedArticle.Visibility.PUBLIC in allowed_visibilities
        else 0
    )
    internal_pending_count = (
        base_pending_queryset.filter(visibility=SuggestedArticle.Visibility.INTERNAL).count()
        if SuggestedArticle.Visibility.INTERNAL in allowed_visibilities
        else 0
    )
    page_obj = paginate_articles(request, article_queryset, per_page=20)

    filter_query_suffix = f"&visibility={requested_visibility}"
    if search_query:
        filter_query_suffix += f"&q={quote(search_query)}"

    if requested_visibility == SuggestedArticle.Visibility.INTERNAL:
        pending_page_title = _("Manage Internal Pending Articles")
        pending_scope_description = _("Review internal articles and internal pending updates waiting for approval.")
    elif requested_visibility == SuggestedArticle.Visibility.PUBLIC:
        pending_page_title = _("Manage Pending Articles")
        pending_scope_description = _("Review general articles and public pending updates waiting for approval.")
    else:
        pending_page_title = _("Manage Pending Articles")
        pending_scope_description = _("Review general and internal articles or pending updates waiting for approval.")

    return render(request, "admin_pending_articles.html", {
        "articles": page_obj.object_list,
        "deletion_requests": [],
        "page_obj": page_obj,
        "pending_search_query": search_query,
        "pending_result_count": article_queryset.count(),
        "total_pending_article_count": total_pending_article_count,
        "total_article_review_count": total_article_review_count,
        "total_deletion_request_count": 0,
        "public_pending_count": public_pending_count,
        "internal_pending_count": internal_pending_count,
        "is_pending_search": bool(search_query),
        "pending_visibility": requested_visibility,
        "pending_page_title": pending_page_title,
        "pending_scope_description": pending_scope_description,
        "pending_search_action": reverse("manage_pending_articles"),
        "pending_allowed_public": SuggestedArticle.Visibility.PUBLIC in allowed_visibilities,
        "pending_allowed_internal": SuggestedArticle.Visibility.INTERNAL in allowed_visibilities,
        "pending_show_visibility_filter": len(allowed_visibilities) > 1,
        "pending_filter_query_suffix": filter_query_suffix,
        "pending_is_all_scope": requested_visibility == "all",
        "is_internal_space": requested_visibility == SuggestedArticle.Visibility.INTERNAL,
    })


@main_site_login_required
def manage_pending_articles(request):
    return _manage_pending_articles_for_allowed_visibilities(request)


@main_site_login_required
def manage_internal_pending_articles(request):
    if not user_can_manage_internal_articles(request.user):
        raise Http404("Article not found")
    return redirect(f"{reverse('manage_pending_articles')}?visibility=internal")


@admin_tools_required
def manage_orphan_articles(request):
    """Scan articles with no active owner and let admins assign or delete them safely."""
    UserModel = get_user_model()
    search_query = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()

    orphan_filter = Q(owner__isnull=True) | Q(owner__is_active=False)
    orphan_queryset = SuggestedArticle.objects.select_related("owner").filter(orphan_filter)
    total_orphan_article_count = orphan_queryset.count()

    if status_filter and status_filter in SuggestedArticle.Status.values:
        orphan_queryset = orphan_queryset.filter(status=status_filter)

    if search_query:
        orphan_queryset = orphan_queryset.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(keywords__icontains=search_query)
            | Q(status__icontains=search_query)
            | Q(filename__icontains=search_query)
            | Q(author_username_snapshot__icontains=search_query)
            | Q(author_name_snapshot__icontains=search_query)
            | Q(author_email_snapshot__icontains=search_query)
            | Q(owner__username__icontains=search_query)
            | Q(owner__email__icontains=search_query)
        )

    active_users = UserModel.objects.filter(is_active=True).order_by("username")

    if request.method == "POST":
        try:
            action = (request.POST.get("action") or "").strip().lower()

            if action not in {"assign", "delete"}:
                messages.error(request, _("Please use the Assign selected or Delete selected button."))
                return redirect("manage_orphan_articles")

            selected_ids = request.POST.getlist("selected_articles")
            clean_selected_ids = []
            for selected_id in selected_ids:
                try:
                    clean_selected_ids.append(int(selected_id))
                except (TypeError, ValueError):
                    continue

            if not clean_selected_ids:
                messages.warning(request, _("Please select at least one orphan article first."))
                return redirect("manage_orphan_articles")

            selected_articles = list(
                SuggestedArticle.objects.select_related("owner").filter(
                    orphan_filter,
                    pk__in=clean_selected_ids,
                ).order_by("title")
            )

            if not selected_articles:
                messages.warning(
                    request,
                    _("The selected articles are no longer orphan articles or no longer exist. Please refresh and try again."),
                )
                return redirect("manage_orphan_articles")

            skipped_count = len(set(clean_selected_ids)) - len(selected_articles)
            if skipped_count > 0:
                messages.info(
                    request,
                    _("Some selected articles were skipped because they are no longer orphan articles."),
                )

            if action == "assign":
                target_user_id = (request.POST.get("target_user") or "").strip()
                target_user_value = (request.POST.get("target_user_lookup") or "").strip()

                target_user = None

                if request.POST.get("confirm") == "yes":
                    if not target_user_id:
                        messages.error(request, _("The selected target user was missing. Please choose the user again."))
                        return redirect("manage_orphan_articles")

                    try:
                        target_user = active_users.get(pk=int(target_user_id))
                    except (TypeError, ValueError, UserModel.DoesNotExist):
                        messages.error(
                            request,
                            _("The selected target user is invalid or inactive. Please choose an active user."),
                        )
                        return redirect("manage_orphan_articles")
                else:
                    if not target_user_value:
                        messages.error(request, _("Please enter a username or email to assign the selected articles to."))
                        return redirect("manage_orphan_articles")

                    matching_users = list(
                        active_users.filter(
                            Q(username__iexact=target_user_value) | Q(email__iexact=target_user_value)
                        )[:2]
                    )

                    if len(matching_users) == 0:
                        messages.error(
                            request,
                            _("No active user was found for '%(user)s'. Please enter a valid username or email.") % {
                                "user": target_user_value,
                            },
                        )
                        return redirect("manage_orphan_articles")

                    if len(matching_users) > 1:
                        messages.error(
                            request,
                            _("More than one active user matched '%(user)s'. Please use the exact username instead.") % {
                                "user": target_user_value,
                            },
                        )
                        return redirect("manage_orphan_articles")

                    target_user = matching_users[0]

                target_user_label = target_user.get_username()
                if target_user.email:
                    target_user_label = f"{target_user.get_username()} ({target_user.email})"

                if request.POST.get("confirm") != "yes":
                    return render(request, "admin_orphan_articles_confirm.html", {
                        "action": "assign",
                        "articles": selected_articles,
                        "target_user": target_user,
                        "target_user_label": target_user_label,
                        "return_url": reverse("manage_orphan_articles"),
                    })

                try:
                    with transaction.atomic():
                        assigned_count = 0
                        for article in selected_articles:
                            article.owner = target_user
                            article.save()
                            write_article_files(article)
                            assigned_count += 1
                except Exception:
                    logger.exception("Failed to assign orphan articles.")
                    messages.error(
                        request,
                        _("The articles could not be assigned safely. No assignment was applied. Please check the logs and try again."),
                    )
                    return redirect("manage_orphan_articles")

                log_activity(
                    request,
                    ActivityLog.EventType.ARTICLE_ORPHAN_ASSIGNED,
                    details={
                        "assigned_count": assigned_count,
                        "target_user": target_user.get_username(),
                        "target_user_id": target_user.pk,
                        "article_ids": [article.pk for article in selected_articles],
                        "article_titles": [article.title for article in selected_articles],
                    },
                )

                messages.success(
                    request,
                    _("Assigned %(count)s orphan article(s) to %(user)s.") % {
                        "count": assigned_count,
                        "user": target_user.get_username(),
                    },
                )
                return redirect("manage_orphan_articles")

            if action == "delete":
                if request.POST.get("confirm") != "yes":
                    return render(request, "admin_orphan_articles_confirm.html", {
                        "action": "delete",
                        "articles": selected_articles,
                        "return_url": reverse("manage_orphan_articles"),
                    })

                deleted_count = 0
                failed_titles = []

                for article in selected_articles:
                    title = article.title
                    try:
                        delete_article_files(article)
                        article.delete()
                        deleted_count += 1
                    except Exception:
                        logger.exception("Failed to delete orphan article '%s'.", title)
                        failed_titles.append(title)

                if deleted_count:
                    log_activity(
                        request,
                        ActivityLog.EventType.ARTICLE_ORPHAN_DELETED,
                        details={
                            "deleted_count": deleted_count,
                            "failed_count": len(failed_titles),
                            "selected_article_ids": clean_selected_ids,
                            "deleted_titles": [article.title for article in selected_articles if article.title not in failed_titles],
                            "failed_titles": failed_titles[:10],
                        },
                    )
                    messages.success(request, _("Deleted %(count)s orphan article(s).") % {"count": deleted_count})

                if failed_titles:
                    messages.error(
                        request,
                        _("Some selected articles could not be deleted: %(titles)s") % {
                            "titles": ", ".join(failed_titles[:5]),
                        },
                    )

                if not deleted_count and not failed_titles:
                    messages.warning(request, _("No orphan articles were deleted. Please refresh and try again."))

                return redirect("manage_orphan_articles")

            messages.error(request, _("Invalid orphan article action."))
            return redirect("manage_orphan_articles")

        except Exception:
            logger.exception("Unexpected error in orphan article admin tool.")
            messages.error(
                request,
                _("Something went wrong while processing the orphan article action. No changes were applied. Please check your selection and try again."),
            )
            return redirect("manage_orphan_articles")

    orphan_queryset = orphan_queryset.order_by("status", "-updated_at", "title")
    page_obj = paginate_articles(request, orphan_queryset, per_page=20)

    return render(request, "admin_orphan_articles.html", {
        "articles": page_obj.object_list,
        "deletion_requests": [],
        "page_obj": page_obj,
        "orphan_search_query": search_query,
        "status_filter": status_filter,
        "status_choices": SuggestedArticle.Status.choices,
        "orphan_result_count": orphan_queryset.count(),
        "total_orphan_article_count": total_orphan_article_count,
        "is_orphan_search": bool(search_query or status_filter),
        "active_users": active_users,
    })
