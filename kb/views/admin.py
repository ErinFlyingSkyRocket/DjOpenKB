from .services import *
from django.contrib.auth.models import User
from django.db.models import Q
from django.db import transaction



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
            messages.warning(request, "No stray upload files were selected for deletion.")
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
                errors.append(f"Skipped invalid path: {item['filename']}")
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


@admin_tools_required
def admin_bulk_articles(request):
    """Admin page for importing/exporting article bundles."""
    return render(request, "admin_bulk_articles.html")


@admin_tools_required
def export_articles_zip(request):
    """Export all Django-managed articles plus referenced uploaded files as a zip."""
    manifest = build_bulk_export_payload()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr(
            "README.txt",
            (
                "DjOpenKB bulk article export.\n"
                "Import this zip from My Profile -> Admin tools -> Bulk import/export articles.\n"
                "Articles are stored in manifest.json and articles/*.md.\n"
                "Referenced uploaded files are stored in uploads/.\n"
            ),
        )

        for article in manifest["articles"]:
            article_filename = safe_uploaded_filename(article.get("filename")) or f"{slugify_title(article.get('title') or 'article')}.md"
            archive.writestr(f"articles/{article_filename}", build_article_markdown(type("ArticleExport", (), article)))

        upload_dir = get_openkb_uploads_dir().resolve()
        exported_uploads = set()

        for filename in manifest.get("uploads", []):
            filename = safe_uploaded_filename(filename)
            if not filename or filename in exported_uploads:
                continue

            file_path = (upload_dir / filename).resolve()
            try:
                file_path.relative_to(upload_dir)
            except ValueError:
                continue

            if file_path.exists() and file_path.is_file():
                archive.write(file_path, f"uploads/{filename}")
                exported_uploads.add(filename)

    buffer.seek(0)
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="djopenkb-export-{timestamp}.zip"'
    return response


@admin_tools_required
@require_POST
def import_articles_zip(request):
    """Import articles from a zip and assign ownership to the current admin user."""
    if request.method != "POST":
        return redirect("admin_bulk_articles")

    uploaded_zip = request.FILES.get("import_zip")
    if not uploaded_zip:
        messages.error(request, "Please choose a .zip file to import.")
        return redirect("admin_bulk_articles")

    if not uploaded_zip.name.lower().endswith(".zip"):
        messages.error(request, "Only .zip import files are allowed.")
        return redirect("admin_bulk_articles")

    max_upload_size = 100 * 1024 * 1024
    if uploaded_zip.size > max_upload_size:
        messages.error(request, "Import zip is too large. Maximum allowed size is 100 MB.")
        return redirect("admin_bulk_articles")

    try:
        imported_count, errors = import_articles_from_zip(uploaded_zip, owner=request.user)
    except zipfile.BadZipFile:
        messages.error(request, "Invalid zip file.")
        return redirect("admin_bulk_articles")
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("admin_bulk_articles")
    except Exception as error:
        messages.error(request, f"Import failed: {error}")
        return redirect("admin_bulk_articles")

    if imported_count:
        messages.success(request, f"Imported {imported_count} article(s). Owner set to {request.user.get_username()}.")
    else:
        messages.warning(request, "No articles were imported.")

    for error in errors[:10]:
        messages.error(request, error)

    if len(errors) > 10:
        messages.error(request, f"{len(errors) - 10} more import error(s) were hidden.")

    return redirect("admin_bulk_articles")


@admin_tools_required
def manage_pending_articles(request):
    search_query = request.GET.get("q", "").strip()

    article_queryset = SuggestedArticle.objects.select_related("owner").filter(
        status=SuggestedArticle.Status.PENDING
    )
    total_pending_article_count = article_queryset.count()

    if search_query:
        article_queryset = article_queryset.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(keywords__icontains=search_query)
            | Q(review_notes__icontains=search_query)
            | Q(review_notes_history__icontains=search_query)
            | Q(filename__icontains=search_query)
            | Q(owner__username__icontains=search_query)
            | Q(owner__email__icontains=search_query)
            | Q(author_username_snapshot__icontains=search_query)
            | Q(author_email_snapshot__icontains=search_query)
        )

    article_queryset = article_queryset.order_by("created_at", "updated_at")
    page_obj = paginate_articles(request, article_queryset, per_page=20)

    return render(request, "admin_pending_articles.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "pending_search_query": search_query,
        "pending_result_count": article_queryset.count(),
        "total_pending_article_count": total_pending_article_count,
        "is_pending_search": bool(search_query),
    })

@admin_tools_required
def manage_orphan_articles(request):
    """Scan articles with no active owner and let admins assign or delete them safely."""
    User = get_user_model()
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

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

    active_users = User.objects.filter(is_active=True).order_by("username")

    if request.method == "POST":
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
                except (TypeError, ValueError, User.DoesNotExist):
                    messages.error(
                        request,
                        _("The selected target user is invalid or inactive. Please choose an active user."),
                    )
                    return redirect("manage_orphan_articles")
            else:
                if not target_user_value:
                    messages.error(request, _("Please enter a username or email to assign the selected articles to."))
                    return redirect("manage_orphan_articles")

                matching_users = active_users.filter(
                    Q(username__iexact=target_user_value) | Q(email__iexact=target_user_value)
                )

                match_count = matching_users.count()
                if match_count == 0:
                    messages.error(
                        request,
                        _("No active user was found for '%(user)s'. Please enter a valid username or email.") % {
                            "user": target_user_value,
                        },
                    )
                    return redirect("manage_orphan_articles")

                if match_count > 1:
                    messages.error(
                        request,
                        _("More than one active user matched '%(user)s'. Please use the exact username instead.") % {
                            "user": target_user_value,
                        },
                    )
                    return redirect("manage_orphan_articles")

                target_user = matching_users.first()

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

            assigned_count = 0
            failed_titles = []

            try:
                with transaction.atomic():
                    for article in selected_articles:
                        article.owner = target_user
                        article.save()
                        write_article_files(article)
                        assigned_count += 1
            except Exception:
                messages.error(
                    request,
                    _("The articles could not be assigned safely. No assignment was applied. Please check the logs and try again."),
                )
                return redirect("manage_orphan_articles")

            if assigned_count:
                messages.success(
                    request,
                    _("Assigned %(count)s orphan article(s) to %(user)s.") % {
                        "count": assigned_count,
                        "user": target_user.get_username(),
                    },
                )
            else:
                messages.warning(request, _("No orphan articles were assigned. Please refresh and try again."))

            if failed_titles:
                messages.error(
                    request,
                    _("Some articles could not be assigned: %(titles)s") % {
                        "titles": ", ".join(failed_titles[:5]),
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
                    failed_titles.append(title)

            if deleted_count:
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

    orphan_queryset = orphan_queryset.order_by("status", "-updated_at", "title")
    page_obj = paginate_articles(request, orphan_queryset, per_page=20)

    return render(request, "admin_orphan_articles.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "orphan_search_query": search_query,
        "status_filter": status_filter,
        "status_choices": SuggestedArticle.Status.choices,
        "orphan_result_count": orphan_queryset.count(),
        "total_orphan_article_count": total_orphan_article_count,
        "is_orphan_search": bool(search_query or status_filter),
        "active_users": active_users,
    })
