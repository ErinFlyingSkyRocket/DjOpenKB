from .services import *
from ..notifications import (
    NOTIFICATION_KIND_NEW_SUBMISSION,
    NOTIFICATION_KIND_UPDATE_SUBMISSION,
    OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED,
    OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED,
    OWNER_NOTIFICATION_KIND_UPDATE_APPROVED,
    OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED,
    send_article_owner_notification_after_commit,
    send_article_review_notification_after_commit,
)
from collections import Counter
import json
import re
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from urllib.parse import quote

from ..auth_monitoring import build_auth_lockout_ui_context, get_auth_lockout_status
from ..input_limits import (
    ARTICLE_KEYWORDS_MAX_LENGTH,
    ARTICLE_TITLE_MAX_LENGTH,
    FILENAME_MAX_LENGTH,
    URL_MAX_LENGTH,
)


def _article_delete_lockout_context(request, requires_mfa):
    if not requires_mfa:
        return build_auth_lockout_ui_context(
            locked=False,
            retry_after_seconds=0,
            message=_(
                "Too many incorrect MFA codes. Please try again in %(duration)s."
            ),
            prefix="article_delete_mfa_lockout",
        )

    locked, retry_after, _identifier = get_auth_lockout_status(
        request,
        user=request.user,
        purpose="mfa",
    )
    return build_auth_lockout_ui_context(
        locked=locked,
        retry_after_seconds=retry_after,
        message=_(
            "Too many incorrect MFA codes. Please try again in %(duration)s."
        ),
        prefix="article_delete_mfa_lockout",
    )


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



def _article_creation_workspace_allowed_visibilities(visibility_choices):
    return {
        str(choice.get("value"))
        for choice in (visibility_choices or [])
        if isinstance(choice, dict) and choice.get("value")
    }


def _get_or_create_article_creation_workspace(request, *, visibility_choices, requested_visibility):
    """Return the signed-in user's single persistent New Article checkpoint."""
    allowed_visibilities = _article_creation_workspace_allowed_visibilities(visibility_choices)
    if requested_visibility not in allowed_visibilities:
        requested_visibility = (
            SuggestedArticle.Visibility.PUBLIC
            if SuggestedArticle.Visibility.PUBLIC in allowed_visibilities
            else sorted(allowed_visibilities)[0]
        )

    requested_workspace_id = (request.GET.get("workspace") or "").strip()
    if requested_workspace_id:
        with transaction.atomic():
            workspace = get_owned_article_creation_workspace(
                request.user,
                requested_workspace_id,
                for_update=True,
            )
            if workspace is None:
                raise Http404("Article workspace not found")
            if workspace.visibility not in allowed_visibilities:
                workspace.visibility = requested_visibility
                workspace.save(update_fields=["visibility", "updated_at"])
        request.session[ARTICLE_CREATION_WORKSPACE_SESSION_KEY] = str(workspace.pk)
        request.session.modified = True
        return workspace, False

    try:
        with transaction.atomic():
            workspace = (
                ArticleCreationWorkspace.objects
                .select_for_update()
                .filter(owner=request.user)
                .first()
            )
            created = workspace is None
            if workspace is None:
                workspace = ArticleCreationWorkspace.objects.create(
                    owner=request.user,
                    visibility=requested_visibility,
                )
            elif workspace.visibility not in allowed_visibilities:
                # Preserve the temporary content but never retain a scope the
                # account is no longer authorised to create.
                workspace.visibility = requested_visibility
                workspace.save(update_fields=["visibility", "updated_at"])
            elif not workspace.is_dirty and workspace.visibility != requested_visibility:
                workspace.visibility = requested_visibility
                workspace.save(update_fields=["visibility", "updated_at"])
    except IntegrityError:
        workspace = ArticleCreationWorkspace.objects.get(owner=request.user)
        created = False

    request.session[ARTICLE_CREATION_WORKSPACE_SESSION_KEY] = str(workspace.pk)
    request.session.modified = True
    return workspace, created




def _article_edit_workspace_mode(is_review_mode):
    return (
        ArticleEditWorkspace.EditorMode.REVIEW
        if is_review_mode
        else ArticleEditWorkspace.EditorMode.EDIT
    )


def _article_edit_workspace_baseline(article, user, *, is_review_mode):
    """Return the current saved values used to initialise an edit checkpoint."""
    can_review = user_can_review_article(user, article, review_mode=is_review_mode)
    has_staged_update = bool(getattr(article, "has_staged_update", False))
    if has_staged_update:
        title = article.pending_update_title or article.title
        body = article.pending_update_body or article.body
        keywords = article.pending_update_keywords or article.keywords
        image_assets = list(
            article.pending_update_image_assets
            or extract_article_image_filenames(body)
        )
    else:
        title = article.title
        body = article.body
        keywords = article.keywords
        image_assets = list(article.image_assets or extract_article_image_filenames(body))

    if can_review:
        if has_staged_update:
            status = (
                SuggestedArticle.Status.FAILED
                if article.update_status == SuggestedArticle.UpdateStatus.FAILED
                else SuggestedArticle.Status.PENDING
            )
        else:
            status = article.status
        review_notes = article.review_notes
    else:
        status = ""
        review_notes = ""

    return {
        "title": title or "",
        "body": body or "",
        "keywords": keywords or "",
        "visibility": article.visibility,
        "status": status or "",
        "review_notes": review_notes or "",
        "image_assets": list(dict.fromkeys(image_assets)),
        "article_approved_at_snapshot": article.approved_at,
    }


def _get_or_create_article_edit_workspace(
    request,
    article,
    *,
    is_review_mode,
    refresh_clean_workspace=True,
    restore_manual_draft=False,
):
    """Return the owner-scoped context for editing one existing article.

    Existing-article changes are never autosaved. Normally a fresh GET discards
    the temporary workspace and reloads the latest saved article/staged update.
    Managers/Admins may explicitly save a personal draft for a published article;
    only that deliberate draft (``is_dirty=True``) is restored on a later GET.
    """
    editor_mode = _article_edit_workspace_mode(is_review_mode)
    baseline = _article_edit_workspace_baseline(
        article,
        request.user,
        is_review_mode=is_review_mode,
    )

    if refresh_clean_workspace:
        with transaction.atomic():
            existing = (
                ArticleEditWorkspace.objects.select_for_update()
                .filter(
                    owner=request.user,
                    article=article,
                    editor_mode=editor_mode,
                )
                .first()
            )
            if existing is not None and not (restore_manual_draft and existing.is_dirty):
                # Existing-article edits are manual-save only. A fresh GET must
                # never restore unsaved text or temporary image changes. The one
                # exception is an explicit personal draft saved by a Manager/Admin.
                discard_article_edit_workspace(request, existing)

    try:
        with transaction.atomic():
            workspace = (
                ArticleEditWorkspace.objects.select_for_update()
                .filter(
                    owner=request.user,
                    article=article,
                    editor_mode=editor_mode,
                )
                .first()
            )
            if workspace is None:
                workspace = ArticleEditWorkspace.objects.create(
                    owner=request.user,
                    article=article,
                    editor_mode=editor_mode,
                    **baseline,
                )
    except IntegrityError:
        workspace = ArticleEditWorkspace.objects.get(
            owner=request.user,
            article=article,
            editor_mode=editor_mode,
        )
    return workspace


def _parse_article_approval_snapshot(value):
    """Parse the hidden approval baseline emitted by an older editor page."""
    value = (value or "").strip()
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _article_approval_took_precedence(article, approved_at_snapshot):
    """Return True when a newer published version replaced this editor baseline."""
    current_approved_at = getattr(article, "approved_at", None)
    if article.status != SuggestedArticle.Status.PUBLISHED or not current_approved_at:
        return False
    if approved_at_snapshot is None:
        return True
    return current_approved_at > approved_at_snapshot


def _approval_precedence_error_message():
    return _(
        "The article version opened in this editor was approved or published while you were editing. "
        "Your unsaved changes were not applied to the article. Reload the latest article before saving "
        "or submitting another update."
    )


def _render_suggest_form_for_visibility(request, *, visibility, can_publish_directly, visibility_choices=None, workspace=None, extra_context=None):
    visibility = normalize_article_visibility(visibility)
    visibility_choices = visibility_choices or article_visibility_choices_for_user(request.user, action="add")
    show_visibility_selector = bool(
        len(visibility_choices) > 1
        and (user_can_add_internal_articles(request.user) or user_can_use_admin_tools(request.user))
    )
    context = {
        "can_publish_directly": can_publish_directly,
        "article_image_upload_limit": get_article_image_upload_limit(),
        "article_video_max_width_px": get_article_video_max_width_px(),
        "article_body_character_limit": get_article_body_character_limit(),
        "article_keyword_limit": get_article_keyword_limit(),
        "keyword_suggestion_catalog_json": get_keyword_suggestion_catalog_json(visibility=visibility, user=request.user),
        "article_visibility": visibility,
        "article_visibility_label": article_visibility_label(visibility),
        "visibility_choices": visibility_choices,
        "show_visibility_selector": show_visibility_selector,
        "is_internal_article_form": visibility == SuggestedArticle.Visibility.INTERNAL,
        "suggest_form_action": reverse("suggest"),
        "suggest_page_title": _("Add Article"),
        "suggest_submit_label": _("Submit article"),
        "article_creation_workspace_id": str(workspace.pk) if workspace else "",
        "article_creation_workspace_dirty": bool(workspace and workspace.is_dirty),
        "title_value": workspace.title if workspace else "",
        "body_value": workspace.body if workspace else "",
        "keywords_value": workspace.keywords if workspace else "",
        "existing_images_json": json.dumps(
            get_article_image_cards_from_filenames(
                article_creation_workspace_assets(workspace),
                existing=False,
            )
        ) if workspace else "[]",
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

    requested_visibility = choose_requested_article_visibility(
        request.user,
        request.POST.get("article_visibility") if request.method == "POST" else request.GET.get("visibility"),
        action="add",
        default=SuggestedArticle.Visibility.PUBLIC,
    )

    if request.method == "GET":
        workspace, _created = _get_or_create_article_creation_workspace(
            request,
            visibility_choices=visibility_choices,
            requested_visibility=requested_visibility,
        )
        visibility = workspace.visibility
    else:
        workspace_id = (request.POST.get("workspace_id") or "").strip()
        workspace = get_owned_article_creation_workspace(request.user, workspace_id)
        if workspace is None:
            raise Http404("Article workspace not found")
        visibility = choose_requested_article_visibility(
            request.user,
            request.POST.get("article_visibility"),
            action="add",
            default=workspace.visibility,
        )

    def render_suggest_form(extra_context=None):
        return _render_suggest_form_for_visibility(
            request,
            visibility=visibility,
            can_publish_directly=can_publish_directly,
            visibility_choices=visibility_choices,
            workspace=workspace,
            extra_context=extra_context,
        )

    if request.method == "GET":
        return render_suggest_form()

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()
    draft_image_assets = extract_article_image_filenames(body)

    if len(title) > ARTICLE_TITLE_MAX_LENGTH or len(keywords_raw) > ARTICLE_KEYWORDS_MAX_LENGTH:
        return render_suggest_form({
            "error": _("The temporary article contains an oversized field."),
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
        })
    try:
        validate_article_body(body)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_suggest_form({
            "error": message,
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
        })

    with transaction.atomic():
        workspace = get_owned_article_creation_workspace(
            request.user,
            workspace_id,
            for_update=True,
        )
        if workspace is None:
            raise Http404("Article workspace not found")
        workspace.title = title
        workspace.body = body
        workspace.keywords = keywords_raw
        workspace.visibility = visibility
        workspace.image_assets = article_creation_workspace_assets(workspace)
        workspace.is_dirty = True
        workspace.save(update_fields=[
            "title",
            "body",
            "keywords",
            "visibility",
            "image_assets",
            "is_dirty",
            "updated_at",
        ])
    draft_images_json = json.dumps(
        get_article_image_cards_from_filenames(
            article_creation_workspace_assets(workspace),
            existing=False,
        )
    )
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
            "existing_images_json": draft_images_json,
        })

    try:
        body = validate_article_body(body)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_suggest_form({
            "error": message,
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": draft_images_json,
        })

    try:
        keywords_raw = validate_article_keywords(keywords_raw)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_suggest_form({
            "error": message,
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": draft_images_json,
        })

    try:
        validate_article_video_links_for_anonymous_access(body)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_suggest_form({
            "error": message,
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": draft_images_json,
        })

    duplicate_article = find_duplicate_article_by_title(title)
    if duplicate_article:
        return render_suggest_form({
            "error": duplicate_title_error_message(title),
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": draft_images_json,
        })

    new_image_assets = draft_image_assets
    try:
        new_image_assets = validate_article_creation_workspace_image_references(workspace, body)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_suggest_form({
            "error": message,
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": json.dumps(
                get_article_image_cards_from_filenames(new_image_assets, existing=False)
            ),
        })

    article = SuggestedArticle(
        owner=request.user,
        title=title,
        body=body,
        keywords=keywords_raw,
        visibility=visibility,
        status=status,
        approved_by=request.user if status == SuggestedArticle.Status.PUBLISHED else None,
        approved_at=timezone.now() if status == SuggestedArticle.Status.PUBLISHED else None,
        image_assets=new_image_assets,
    )
    filename = ensure_article_filename(article)
    article.wiki_path = (
        f"internal/sources/{filename}"
        if visibility == SuggestedArticle.Visibility.INTERNAL
        else f"sources/{filename}"
    )
    article.raw_path = (
        f"raw/internal/{filename}"
        if visibility == SuggestedArticle.Visibility.INTERNAL
        else f"raw/{filename}"
    )
    try:
        with transaction.atomic():
            locked_workspace = get_owned_article_creation_workspace(
                request.user,
                workspace_id,
                for_update=True,
            )
            if locked_workspace is None:
                raise ValidationError(_("This temporary article is no longer available. Open New Article and try again."))
            # Recheck ownership while the workspace row is locked so a
            # concurrent upload/discard request cannot race article creation.
            article.image_assets = validate_article_creation_workspace_image_references(
                locked_workspace,
                body,
            )
            article.full_clean()
            article.save()
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_suggest_form({
            "error": message,
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": draft_images_json,
        })
    except IntegrityError:
        return render_suggest_form({
            "error": duplicate_title_error_message(title),
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "existing_images_json": draft_images_json,
        })
    write_article_files(article)
    sync_article_image_assets(article, old_assets=[])
    # Re-lock and refresh before final cleanup so an upload that completed
    # while the article was being validated cannot become an untracked stray.
    with transaction.atomic():
        latest_workspace = get_owned_article_creation_workspace(
            request.user,
            workspace_id,
            for_update=True,
        )
        if latest_workspace is not None:
            finalize_article_creation_workspace(
                request,
                latest_workspace,
                article.image_assets,
            )

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

    # Only a non-admin article submission enters the reviewer queue. Direct
    # admin publication and private drafts intentionally produce no email.
    if status == SuggestedArticle.Status.PENDING:
        send_article_review_notification_after_commit(
            request,
            article,
            NOTIFICATION_KIND_NEW_SUBMISSION,
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

    requested_status = (request.GET.get("status") or "all").strip().lower()
    allowed_status_filters = {
        "all",
        SuggestedArticle.Status.PUBLISHED,
        SuggestedArticle.Status.PENDING,
        SuggestedArticle.Status.FAILED,
        SuggestedArticle.Status.DRAFT,
        "pending_update",
        "failed_update",
    }
    if requested_status not in allowed_status_filters:
        requested_status = "all"

    article_queryset = article_workspace_queryset_for_user(request.user)
    if requested_visibility != "all":
        article_queryset = article_queryset.filter(visibility=requested_visibility)

    # Keep this count before status/text filtering so the page can show the
    # number of matching articles against the selected visibility scope.
    total_user_article_count = article_queryset.count()

    # Filter by the status users actually see in the table. Published articles
    # with a submitted/rejected staged update are shown under their update
    # status rather than under Published.
    if requested_status == "pending_update":
        article_queryset = article_queryset.filter(
            status=SuggestedArticle.Status.PUBLISHED,
            update_status=SuggestedArticle.UpdateStatus.PENDING,
        )
    elif requested_status == "failed_update":
        article_queryset = article_queryset.filter(
            status=SuggestedArticle.Status.PUBLISHED,
            update_status=SuggestedArticle.UpdateStatus.FAILED,
        )
    elif requested_status == SuggestedArticle.Status.PUBLISHED:
        article_queryset = article_queryset.filter(
            status=SuggestedArticle.Status.PUBLISHED,
            update_status=SuggestedArticle.UpdateStatus.NONE,
        )
    elif requested_status in {
        SuggestedArticle.Status.PENDING,
        SuggestedArticle.Status.FAILED,
        SuggestedArticle.Status.DRAFT,
    }:
        article_queryset = article_queryset.filter(status=requested_status)

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
    if requested_status != "all":
        filter_query_suffix += f"&status={requested_status}"
    if search_query:
        filter_query_suffix += f"&q={quote(search_query)}"

    clear_search_url = reverse("edit_my_suggestions")
    clear_search_params = []
    if requested_visibility != "all":
        clear_search_params.append(f"visibility={quote(requested_visibility)}")
    if requested_status != "all":
        clear_search_params.append(f"status={quote(requested_status)}")
    if clear_search_params:
        clear_search_url += "?" + "&".join(clear_search_params)

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
        "profile_status_filter": requested_status,
        "profile_allowed_public": SuggestedArticle.Visibility.PUBLIC in allowed_visibilities,
        "profile_allowed_internal": SuggestedArticle.Visibility.INTERNAL in allowed_visibilities,
        "profile_show_visibility_filter": len(allowed_visibilities) > 1,
        "profile_filter_query_suffix": filter_query_suffix,
        "profile_clear_search_url": clear_search_url,
        "profile_has_active_filters": bool(search_query or requested_status != "all"),
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
    can_restore_personal_draft = bool(
        not is_review_mode
        and user_can_review_article(request.user, article, review_mode=False)
        and article.status == SuggestedArticle.Status.PUBLISHED
        and not bool(getattr(article, "has_staged_update", False))
    )
    edit_workspace = _get_or_create_article_edit_workspace(
        request,
        article,
        is_review_mode=is_review_mode,
        refresh_clean_workspace=request.method == "GET",
        restore_manual_draft=can_restore_personal_draft,
    )

    def render_edit_form(extra_context=None):
        extra_context = extra_context or {}
        can_review_article = user_can_review_article(request.user, article, review_mode=is_review_mode)
        has_staged_update = bool(getattr(article, "has_staged_update", False))
        pending_update_review = can_review_article and has_staged_update
        has_saved_update_draft = has_staged_update and article.update_status == SuggestedArticle.UpdateStatus.NONE
        baseline = _article_edit_workspace_baseline(
            article,
            request.user,
            is_review_mode=is_review_mode,
        )
        has_personal_edit_draft = bool(
            can_restore_personal_draft
            and edit_workspace is not None
            and edit_workspace.is_dirty
        )
        draft_title = edit_workspace.title if has_personal_edit_draft else baseline["title"]
        draft_body = edit_workspace.body if has_personal_edit_draft else baseline["body"]
        draft_keywords = edit_workspace.keywords if has_personal_edit_draft else baseline["keywords"]
        draft_review_notes = edit_workspace.review_notes if has_personal_edit_draft else baseline["review_notes"]
        draft_visibility = edit_workspace.visibility if has_personal_edit_draft else baseline["visibility"]

        edit_title = extra_context.get("title_value", draft_title)
        edit_body = extra_context.get("body_value", draft_body)
        edit_keywords = extra_context.get("keywords_value", draft_keywords)
        # A personal draft never stores a future approval/rejection decision.
        edit_status = extra_context.get("status_value", baseline["status"])
        edit_review_notes = extra_context.get("review_notes_value", draft_review_notes)
        edit_visibility = extra_context.get("article_visibility", draft_visibility)

        back_url = return_url or reverse(fallback_view_name)
        can_edit_content = user_can_edit_article_content(request.user, article, review_mode=is_review_mode)
        can_change_visibility = user_can_change_article_visibility(request.user, article)
        visibility_choices = article_visibility_choices_for_user(request.user, action="add") if can_change_visibility else []
        allowed_statuses = allowed_article_statuses_for_admin_edit(article, user=request.user) if can_review_article else set()
        if "article_edit_approved_at_snapshot_value" in extra_context:
            approved_at_snapshot_value = extra_context["article_edit_approved_at_snapshot_value"]
        else:
            approved_at_snapshot = (
                edit_workspace.article_approved_at_snapshot
                if has_personal_edit_draft
                else baseline["article_approved_at_snapshot"]
            )
            approved_at_snapshot_value = approved_at_snapshot.isoformat() if approved_at_snapshot else ""

        context = {
            "article": article,
            "current_status": extra_context.get("current_status", article.status),
            "review_notes_value": edit_review_notes,
            "review_notes_history": get_review_notes_history(article),
            "show_pending_failed_comments": article.status in {SuggestedArticle.Status.DRAFT, SuggestedArticle.Status.FAILED} and bool(article.review_notes),
            "existing_images_json": json.dumps(get_article_edit_workspace_image_cards(edit_workspace)),
            "article_image_upload_limit": get_article_image_upload_limit(),
            "article_video_max_width_px": get_article_video_max_width_px(),
            "article_body_character_limit": get_article_body_character_limit(),
            "article_keyword_limit": get_article_keyword_limit(),
            "keyword_suggestion_catalog_json": get_keyword_suggestion_catalog_json(visibility=edit_visibility, user=request.user),
            "return_url": return_url,
            "back_url": back_url,
            "title_value": edit_title,
            "body_value": edit_body,
            "keywords_value": edit_keywords,
            "is_pending_update_review": pending_update_review,
            "current_update_status": article.update_status,
            "edit_status_value": edit_status,
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
            "has_personal_edit_draft": has_personal_edit_draft,
            "show_personal_edit_draft_actions": can_restore_personal_draft,
            "article_visibility": edit_visibility,
            "article_edit_workspace_id": str(edit_workspace.pk),
            "article_edit_approved_at_snapshot_value": approved_at_snapshot_value,
            "approval_precedence_conflict": bool(extra_context.get("approval_precedence_conflict", False)),
            "article_visibility_label": article.visibility_label,
            "is_internal_article_form": article.is_internal,
            "allow_status_draft": SuggestedArticle.Status.DRAFT in allowed_statuses,
            "allow_status_pending": SuggestedArticle.Status.PENDING in allowed_statuses,
            "allow_status_failed": SuggestedArticle.Status.FAILED in allowed_statuses,
            "allow_status_published": SuggestedArticle.Status.PUBLISHED in allowed_statuses,
        }
        context.update(extra_context)
        return render(request, "suggest_edit.html", context)

    def render_approval_precedence_conflict(*, snapshot_value=None):
        baseline = _article_edit_workspace_baseline(
            article,
            request.user,
            is_review_mode=is_review_mode,
        )
        if snapshot_value is None:
            snapshot = baseline["article_approved_at_snapshot"]
            snapshot_value = snapshot.isoformat() if snapshot else ""
        return render_edit_form({
            "error": _approval_precedence_error_message(),
            "approval_precedence_conflict": True,
            "article_edit_approved_at_snapshot_value": snapshot_value,
            "title_value": request.POST.get("frm_kb_title", baseline["title"]),
            "body_value": request.POST.get("frm_kb_body", baseline["body"]),
            "keywords_value": request.POST.get("frm_kb_keywords", baseline["keywords"]),
            "status_value": request.POST.get("status", baseline["status"]),
            "current_status": request.POST.get("status", article.status),
            "review_notes_value": request.POST.get("review_notes", baseline["review_notes"]),
            "article_visibility": request.POST.get("article_visibility", baseline["visibility"]),
        })

    if request.method == "GET":
        return render_edit_form()

    submitted_workspace_id = (request.POST.get("edit_workspace_id") or "").strip()
    submitted_approval_snapshot_value = (
        request.POST.get("article_edit_approved_at_snapshot") or ""
    ).strip()
    submitted_approval_snapshot = _parse_article_approval_snapshot(
        submitted_approval_snapshot_value
    )
    # Preserve compatibility with forms or tests opened before the checkpoint
    # field was introduced. The server-created workspace above remains the
    # authoritative owner-scoped context; a supplied non-empty UUID must still
    # match exactly.
    if not submitted_workspace_id:
        submitted_workspace_id = str(edit_workspace.pk)
    with transaction.atomic():
        edit_workspace = get_owned_article_edit_workspace(
            request.user,
            submitted_workspace_id,
            article=article,
            editor_mode=_article_edit_workspace_mode(is_review_mode),
            for_update=True,
        )
        if edit_workspace is None:
            # An older tab may refer to an editor context that was finalised and
            # removed when its earlier version was submitted. If that version has
            # since been approved, show a safe workflow conflict instead of a 404.
            if _article_approval_took_precedence(article, submitted_approval_snapshot):
                return render_approval_precedence_conflict(
                    snapshot_value=submitted_approval_snapshot_value
                )
            raise Http404("Article edit workspace not found")
        effective_approval_snapshot = submitted_approval_snapshot
        if not submitted_approval_snapshot_value:
            # Compatibility for older forms/tests that pre-date the hidden
            # approval snapshot. New pages always submit the explicit value.
            effective_approval_snapshot = edit_workspace.article_approved_at_snapshot
        approval_took_precedence = _article_approval_took_precedence(
            article,
            effective_approval_snapshot,
        )

    submit_action = validate_article_edit_action(
        request.user,
        article,
        request.POST.get("submit_action", "save"),
        review_mode=is_review_mode,
    )

    # Personal-draft operations never modify the shared article, so preserve the
    # user's work even if a newer published version appeared while the editor was
    # open. The original approval snapshot stays attached to the workspace; a later
    # real Save will still be blocked until the user reloads/reverts appropriately.
    if approval_took_precedence and submit_action not in {
        "save_personal_draft",
        "revert_personal_draft",
    }:
        return render_approval_precedence_conflict()

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()

    previous_status = article.status
    previous_update_status = article.update_status

    is_admin_action = user_can_review_article(request.user, article, review_mode=is_review_mode)
    is_published_update_flow = article.status == SuggestedArticle.Status.PUBLISHED and not is_admin_action
    is_admin_pending_update_review = (
        is_admin_action
        and bool(getattr(article, "has_staged_update", False))
    )

    if submit_action == "revert_personal_draft":
        # This is an editor-only reset. It discards only this user's private
        # workspace and never changes the shared article or another user's staged
        # update. The next GET recreates the editor from the latest published row.
        with transaction.atomic():
            locked_workspace = get_owned_article_edit_workspace(
                request.user,
                edit_workspace.pk,
                article=article,
                editor_mode=_article_edit_workspace_mode(is_review_mode),
                for_update=True,
            )
            if locked_workspace is not None:
                discard_article_edit_workspace(request, locked_workspace)
        messages.success(request, _("Reverted to the last published version. Any unpublished update changes were discarded."))
        return redirect(
            f"{reverse('edit_suggestion', kwargs={'article_id': article.pk})}"
            f"?next={quote(return_url, safe='')}"
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

    if (
        submit_action == "revert_published"
        and (is_published_update_flow or is_admin_pending_update_review)
    ):
        # Standard revert operation for every permitted role: discard the
        # unpublished staged update and keep the last approved published version.
        # The published article itself does not need restoring because
        # article.title/body/keywords already contain the approved content.
        if article.review_notes:
            article.archive_current_review_note(actor=request.user, action="update_reverted_to_published")
        article.review_notes = ""
        article.clear_pending_update()
        article.save()
        with transaction.atomic():
            locked_workspace = get_owned_article_edit_workspace(
                request.user,
                edit_workspace.pk,
                article=article,
                editor_mode=_article_edit_workspace_mode(is_review_mode),
                for_update=True,
            )
            if locked_workspace is not None:
                discard_article_edit_workspace(request, locked_workspace)

        log_activity(
            request,
            ActivityLog.EventType.ARTICLE_UPDATED,
            article=article,
            details={
                "action": "revert_pending_update_to_published",
                "previous_status": previous_status,
                "previous_update_status": previous_update_status,
                "is_published_update_flow": is_published_update_flow,
                "is_admin_pending_update_review": is_admin_pending_update_review,
                "is_review_mode": is_review_mode,
            },
        )

        messages.success(
            request,
            _("Reverted to the last published version. Any unpublished update changes were discarded."),
        )

        # A reviewer coming from Manage Pending should return to that queue,
        # because after the staged update is discarded there is nothing left to
        # review. Managers/Admins editing from their own article page retain the
        # existing owner-style behaviour and reload the editor.
        if is_review_mode:
            return redirect(return_url)
        return redirect(
            f"{reverse('edit_suggestion', kwargs={'article_id': article.pk})}"
            f"?next={quote(return_url, safe='')}"
        )

    error_context = {
        "title_value": title,
        "body_value": body,
        "keywords_value": keywords_raw,
        "status_value": status,
        "current_status": status,
        "review_notes_value": review_notes,
        "review_notes_history": get_review_notes_history(article),
        "existing_images_json": json.dumps(get_article_edit_workspace_image_cards(edit_workspace)),
        "article_image_upload_limit": get_article_image_upload_limit(),
        "article_body_character_limit": get_article_body_character_limit(),
        "article_keyword_limit": get_article_keyword_limit(),
        "return_url": return_url,
        "back_url": return_url or reverse(fallback_view_name),
        "article_visibility": requested_visibility,
    }

    if submit_action == "save_personal_draft":
        # Explicit manual draft for Managers/Admins editing an already-published
        # article. This persists only in the current user's ArticleEditWorkspace;
        # it never changes the article row, status, approval metadata, Markdown,
        # notifications, or another editor's draft.
        if not can_edit_content or not can_restore_personal_draft:
            raise Http404("Article action not allowed")
        if len(title) > ARTICLE_TITLE_MAX_LENGTH or len(keywords_raw) > ARTICLE_KEYWORDS_MAX_LENGTH:
            return render_edit_form({
                **error_context,
                "error": _("The temporary article contains an oversized field."),
            })
        try:
            keywords_raw = validate_article_keywords(keywords_raw)
            body = validate_article_body(body)
            draft_image_assets = validate_article_edit_workspace_image_references(
                edit_workspace,
                body,
                article,
            )
        except ValidationError as error:
            message = error.messages[0] if getattr(error, "messages", None) else str(error)
            return render_edit_form({
                **error_context,
                "keywords_value": keywords_raw,
                "body_value": body,
                "error": message,
            })

        with transaction.atomic():
            locked_workspace = get_owned_article_edit_workspace(
                request.user,
                edit_workspace.pk,
                article=article,
                editor_mode=_article_edit_workspace_mode(is_review_mode),
                for_update=True,
            )
            if locked_workspace is None:
                raise Http404("Article edit workspace not found")
            locked_workspace.title = title
            locked_workspace.body = body
            locked_workspace.keywords = keywords_raw
            locked_workspace.visibility = requested_visibility
            # Do not remember a status dropdown choice in a personal draft. A
            # publish/reject/status change always requires an explicit final Save.
            locked_workspace.status = article.status
            locked_workspace.review_notes = ""
            locked_workspace.image_assets = list(dict.fromkeys(draft_image_assets))
            locked_workspace.is_dirty = True
            locked_workspace.save(update_fields=[
                "title",
                "body",
                "keywords",
                "visibility",
                "status",
                "review_notes",
                "image_assets",
                "is_dirty",
                "updated_at",
            ])

        messages.success(request, _("Draft saved successfully."))
        return redirect(
            f"{reverse('edit_suggestion', kwargs={'article_id': article.pk})}"
            f"?next={quote(return_url, safe='')}"
        )

    if is_admin_action and status == SuggestedArticle.Status.FAILED and not review_notes:
        return render_edit_form({
            **error_context,
            "error": _("Please enter Pending failed comments before marking this article as Pending failed."),
        })

    try:
        keywords_raw = validate_article_keywords(keywords_raw)
        error_context["keywords_value"] = keywords_raw
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_edit_form({
            **error_context,
            "error": message,
        })

    if len(title) < 5 or len(body) < 5:
        return render_edit_form({
            **error_context,
            "review_notes_value": request.POST.get("review_notes", article.review_notes),
            "error": _("Article title and body must be at least 5 characters."),
        })

    try:
        body = validate_article_body(body)
        error_context["body_value"] = body
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_edit_form({
            **error_context,
            "error": message,
        })

    try:
        validate_article_video_links_for_anonymous_access(body)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_edit_form({
            **error_context,
            "error": message,
        })

    duplicate_article = find_duplicate_article_by_title(title, exclude_pk=article.pk)
    if duplicate_article:
        return render_edit_form({
            **error_context,
            "error": duplicate_title_error_message(title),
        })

    old_image_assets = list(article.image_assets or extract_article_image_filenames(article.body))
    try:
        new_image_assets = validate_article_edit_workspace_image_references(
            edit_workspace,
            body,
            article,
        )
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
            # Reviewer edited a saved, pending, or rejected update but has not
            # made the final approve/reject decision. Keep the approved version
            # visible and return the staged update to the review queue.
            article.pending_update_title = title
            article.pending_update_body = body
            article.pending_update_keywords = keywords_raw
            article.pending_update_image_assets = new_image_assets
            article.update_status = SuggestedArticle.UpdateStatus.PENDING
            if (
                previous_update_status != SuggestedArticle.UpdateStatus.PENDING
                or not article.update_submitted_at
            ):
                article.update_submitted_at = timezone.now()
            article.update_reviewed_at = None
            if article.review_notes:
                article.archive_current_review_note(actor=request.user, action="update_reopened")
            article.review_notes = ""
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

    try:
        with transaction.atomic():
            current_article = SuggestedArticle.objects.select_for_update().get(pk=article.pk)
            if _article_approval_took_precedence(
                current_article,
                effective_approval_snapshot,
            ):
                article = current_article
                return render_approval_precedence_conflict()
            article.full_clean()
            article.save()
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return render_edit_form({
            **error_context,
            "error": message,
            "is_pending_update_review": is_admin_pending_update_review,
        })
    except IntegrityError:
        return render_edit_form({
            **error_context,
            "error": duplicate_title_error_message(title),
            "is_pending_update_review": is_admin_pending_update_review,
        })
    if visibility_changed:
        # Remove copies from the previous visibility tree only after database
        # validation succeeds. A forged/duplicate update must never delete the
        # currently published Markdown before the save is accepted.
        delete_article_markdown_files(article)
    if write_public_files:
        write_article_files(article)
        sync_article_image_assets(article, old_assets=old_image_assets)
    clear_committed_pending_uploads(request, new_image_assets)
    with transaction.atomic():
        locked_workspace = get_owned_article_edit_workspace(
            request.user,
            edit_workspace.pk,
            article=article,
            editor_mode=_article_edit_workspace_mode(is_review_mode),
            for_update=True,
        )
        if locked_workspace is not None:
            finalize_article_edit_workspace(request, locked_workspace, new_image_assets)

    effective_status = article.status

    if is_admin_pending_update_review and status == SuggestedArticle.Status.PUBLISHED:
        activity_event = ActivityLog.EventType.ARTICLE_APPROVED
    elif is_admin_pending_update_review and status == SuggestedArticle.Status.FAILED:
        activity_event = ActivityLog.EventType.ARTICLE_REJECTED
    elif previous_status != effective_status:
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

    # A reviewer saving their own pending changes must not re-notify the entire
    # review pool. Notify only when an author newly submits or resubmits a normal
    # article, or submits a previously saved/rejected published-article update.
    notification_kind = None
    if (
        not is_admin_action
        and is_published_update_flow
        and submit_action != "save_update_draft"
        and article.update_status == SuggestedArticle.UpdateStatus.PENDING
        and previous_update_status != SuggestedArticle.UpdateStatus.PENDING
    ):
        notification_kind = NOTIFICATION_KIND_UPDATE_SUBMISSION
    elif (
        not is_admin_action
        and not is_published_update_flow
        and effective_status == SuggestedArticle.Status.PENDING
        and previous_status != SuggestedArticle.Status.PENDING
    ):
        notification_kind = NOTIFICATION_KIND_NEW_SUBMISSION

    if notification_kind:
        send_article_review_notification_after_commit(request, article, notification_kind)

    # A review decision has a different audience from a submission: notify only
    # the current article owner after a real final outcome. Keeping review edits
    # pending does not email the owner, and self-review does not create a
    # redundant notification because the delivery helper safely skips it.
    owner_notification_kind = None
    if is_admin_pending_update_review:
        if status == SuggestedArticle.Status.PUBLISHED:
            owner_notification_kind = OWNER_NOTIFICATION_KIND_UPDATE_APPROVED
        elif (
            status == SuggestedArticle.Status.FAILED
            and previous_update_status != SuggestedArticle.UpdateStatus.FAILED
        ):
            # Avoid repeated emails when a reviewer only revises comments on an
            # update that was already marked Pending failed.
            owner_notification_kind = OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED
    elif is_admin_action:
        if effective_status == SuggestedArticle.Status.PUBLISHED and previous_status != SuggestedArticle.Status.PUBLISHED:
            owner_notification_kind = OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED
        elif effective_status == SuggestedArticle.Status.FAILED and previous_status != SuggestedArticle.Status.FAILED:
            owner_notification_kind = OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED

    if owner_notification_kind:
        send_article_owner_notification_after_commit(
            request,
            article,
            owner_notification_kind,
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
                **_article_delete_lockout_context(request, requires_mfa),
            })

        title = article.title
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
        **_article_delete_lockout_context(request, requires_mfa),
    })


@article_image_editor_required
@require_POST
def autosave_article_creation_workspace(request):
    """Persist new-article fields into the user's persistent server checkpoint."""
    workspace_id = (request.POST.get("workspace_id") or "").strip()
    with transaction.atomic():
        workspace = get_owned_article_creation_workspace(
            request.user,
            workspace_id,
            for_update=True,
        )
        if workspace is None:
            return JsonResponse({"error": _("Article workspace not found.")}, status=404)

        allowed_visibilities = _article_creation_workspace_allowed_visibilities(
            article_visibility_choices_for_user(request.user, action="add")
        )
        requested_visibility = (request.POST.get("article_visibility") or workspace.visibility).strip().lower()
        if requested_visibility not in allowed_visibilities:
            return JsonResponse({"error": _("You cannot create an article with this visibility.")}, status=403)

        title = request.POST.get("frm_kb_title", "")
        body = request.POST.get("frm_kb_body", "")
        keywords = request.POST.get("frm_kb_keywords", "")
        try:
            validate_article_body(body)
        except ValidationError as error:
            message = error.messages[0] if getattr(error, "messages", None) else str(error)
            return JsonResponse({"error": message}, status=400)

        if len(title) > ARTICLE_TITLE_MAX_LENGTH or len(keywords) > ARTICLE_KEYWORDS_MAX_LENGTH:
            return JsonResponse({"error": _("The temporary article contains an oversized field.")}, status=400)

        changed = any([
            workspace.title != title,
            workspace.body != body,
            workspace.keywords != keywords,
            workspace.visibility != requested_visibility,
        ])
        workspace.title = title
        workspace.body = body
        workspace.keywords = keywords
        workspace.visibility = requested_visibility
        workspace.image_assets = article_creation_workspace_assets(workspace)
        workspace.is_dirty = bool(workspace.is_dirty or changed or workspace.image_assets)
        workspace.save(update_fields=[
            "title",
            "body",
            "keywords",
            "visibility",
            "image_assets",
            "is_dirty",
            "updated_at",
        ])

    return JsonResponse({
        "saved": True,
        "dirty": workspace.is_dirty,
        "updated_at": workspace.updated_at.isoformat(),
    })


@article_image_editor_required
@require_POST
def discard_article_creation_workspace_view(request):
    """Discard the user's persistent New Article checkpoint and delete its owned images."""
    workspace_id = (request.POST.get("workspace_id") or "").strip()
    with transaction.atomic():
        workspace = get_owned_article_creation_workspace(
            request.user,
            workspace_id,
            for_update=True,
        )
        if workspace is None:
            return JsonResponse({"discarded": True})
        deleted_assets = discard_article_creation_workspace(request, workspace)

    return JsonResponse({"discarded": True, "image_count": len(deleted_assets)})



@article_image_editor_required
@require_POST
def autosave_article_edit_workspace(request, article_id):
    """Existing-article autosave is intentionally disabled.

    New Article keeps its private persistent autosave workspace. Existing article
    edits, pending reviews, and published updates are manual-save only. The route
    remains as a harmless compatibility endpoint for older cached pages.
    """
    raise Http404("Existing article autosave is disabled")


@article_image_editor_required
@require_POST
def discard_article_edit_workspace_view(request, article_id):
    """Discard one user's edit checkpoint and its uncommitted uploads."""
    article = get_object_or_404(SuggestedArticle, pk=article_id)
    is_review_mode = _article_editor_review_mode(request)
    if not user_can_manage_article(request.user, article, review_mode=is_review_mode):
        raise Http404("Article not found")
    if is_review_mode and not user_can_review_article(request.user, article, review_mode=True):
        raise Http404("Article not found")

    workspace_id = (request.POST.get("edit_workspace_id") or "").strip()
    with transaction.atomic():
        workspace = get_owned_article_edit_workspace(
            request.user,
            workspace_id,
            article=article,
            editor_mode=_article_edit_workspace_mode(is_review_mode),
            for_update=True,
        )
        if workspace is None:
            return JsonResponse({"discarded": True})
        deleted_assets = discard_article_edit_workspace(request, workspace)

    return JsonResponse({"discarded": True, "image_count": len(deleted_assets)})


@article_image_editor_required
@require_POST
def validate_article_video_link(request):
    """Validate a video link before the editor inserts it into article Markdown.

    Only supported YouTube and Vimeo links are accepted. Direct external media
    URLs are rejected so the browser never auto-loads a video source that could
    trigger an external authentication prompt.
    """
    url = (request.POST.get("url") or "").strip()
    if not url or len(url) > URL_MAX_LENGTH or not article_video_embed_html(url):
        return JsonResponse({
            "valid": False,
            "error": _("Enter a supported video link."),
        }, status=400)

    return JsonResponse({"valid": True, "url": url})


def _resolve_article_image_editor_context(request, *, for_update=False):
    """Resolve the exact creation workspace, edit workspace, or legacy article context."""
    workspace_id = (request.POST.get("workspace_id") or "").strip()
    edit_workspace_id = (request.POST.get("edit_workspace_id") or "").strip()
    article_id = (request.POST.get("article_id") or "").strip()

    if workspace_id:
        if edit_workspace_id or article_id:
            raise Http404("Article editor context not found")
        workspace = get_owned_article_creation_workspace(
            request.user,
            workspace_id,
            for_update=for_update,
        )
        if workspace is None:
            raise Http404("Article workspace not found")
        allowed_visibilities = set(
            allowed_article_visibility_values_for_user(request.user, action="add")
        )
        if workspace.visibility not in allowed_visibilities:
            raise Http404("Article workspace not found")
        return "workspace", workspace

    if edit_workspace_id:
        edit_workspace = get_owned_article_edit_workspace(
            request.user,
            edit_workspace_id,
            for_update=for_update,
        )
        if edit_workspace is None:
            raise Http404("Article edit checkpoint not found")
        article = edit_workspace.article
        if article_id and str(article.pk) != article_id:
            raise Http404("Article edit checkpoint not found")
        review_mode = edit_workspace.editor_mode == ArticleEditWorkspace.EditorMode.REVIEW
        if not user_can_manage_article(request.user, article, review_mode=review_mode):
            raise Http404("Article not found")
        if review_mode and not user_can_review_article(request.user, article, review_mode=True):
            raise Http404("Article not found")
        return "edit_workspace", edit_workspace

    try:
        article_pk = int(article_id)
    except (TypeError, ValueError):
        raise Http404("Article not found")
    article = get_object_or_404(SuggestedArticle, pk=article_pk)
    if not user_can_manage_article(
        request.user,
        article,
        review_mode=_article_editor_review_mode(request),
    ):
        raise Http404("Article not found")
    return "article", article


@article_image_editor_required
@require_POST
def upload_article_image(request):
    """Upload a validated image into an authorised article editor context."""
    uploaded_file = request.FILES.get("image")
    if not uploaded_file:
        return JsonResponse({"error": _("No image file received.")}, status=400)

    context_kind, context_object = _resolve_article_image_editor_context(request)
    article_limit = get_article_image_upload_limit()
    pending_uploads = request.session.get("pending_article_uploads", [])
    active_pending_uploads = list(dict.fromkeys(
        safe_uploaded_filename(item)
        for item in pending_uploads
        if safe_uploaded_filename(item)
    ))
    if article_limit <= 0:
        return JsonResponse({"error": article_image_limit_error_message(limit=article_limit)}, status=403)

    if context_kind == "workspace":
        current_context_assets = article_creation_workspace_assets(context_object)
        if len(current_context_assets) >= article_limit:
            return JsonResponse({"error": article_image_limit_error_message(limit=article_limit)}, status=400)
    elif context_kind == "edit_workspace":
        current_context_assets = article_edit_workspace_assets(context_object)
        if len(current_context_assets) >= article_limit:
            return JsonResponse({"error": article_image_limit_error_message(limit=article_limit)}, status=400)
    elif len(active_pending_uploads) >= article_limit:
        return JsonResponse({"error": article_image_limit_error_message(limit=article_limit)}, status=400)

    try:
        image_info = validate_article_image_upload(uploaded_file)
    except ValidationError as error:
        message = error.messages[0] if getattr(error, "messages", None) else str(error)
        return JsonResponse({"error": message}, status=400)

    extension = image_info["extension"]
    upload_size = max(0, int(getattr(uploaded_file, "size", 0) or 0))
    count_limit, byte_limit = get_pending_image_upload_limits()
    upload_dir = get_openkb_uploads_dir()
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{uuid.uuid4().hex[:12]}{extension}"
    file_path = upload_dir / filename
    image_log = None

    try:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if context_kind == "workspace":
                context_object = get_owned_article_creation_workspace(
                    request.user,
                    context_object.pk,
                    for_update=True,
                )
                if context_object is None:
                    raise Http404("Article workspace not found")
                current_context_assets = article_creation_workspace_assets(context_object)
                if len(current_context_assets) >= article_limit:
                    return JsonResponse({"error": article_image_limit_error_message(limit=article_limit)}, status=400)
            elif context_kind == "edit_workspace":
                context_object = get_owned_article_edit_workspace(
                    request.user,
                    context_object.pk,
                    for_update=True,
                )
                if context_object is None:
                    raise Http404("Article edit checkpoint not found")
                current_context_assets = article_edit_workspace_assets(context_object)
                if len(current_context_assets) >= article_limit:
                    return JsonResponse({"error": article_image_limit_error_message(limit=article_limit)}, status=400)

            usage = get_user_pending_image_upload_usage(request.user)
            if usage["count"] >= count_limit or usage["bytes"] + upload_size > byte_limit:
                return JsonResponse(
                    {"error": pending_image_upload_quota_error_message(
                        count_limit=count_limit,
                        byte_limit=byte_limit,
                    )},
                    status=400,
                )

            with file_path.open("wb") as destination:
                for chunk in uploaded_file.chunks():
                    destination.write(chunk)

            image_log = ArticleImageUploadLog.objects.create(
                filename=filename,
                original_name=(uploaded_file.name or "")[:255],
                content_type=(getattr(uploaded_file, "content_type", "") or "")[:100],
                size_bytes=upload_size,
                creation_workspace_id=context_object.pk if context_kind == "workspace" else None,
                edit_workspace_id=context_object.pk if context_kind == "edit_workspace" else None,
                editing_article_id=(
                    context_object.article_id
                    if context_kind == "edit_workspace"
                    else context_object.pk if context_kind == "article" else None
                ),
                uploaded_by=request.user,
                upload_ip_address=get_client_ip(request),
                upload_user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            if context_kind == "workspace":
                workspace_assets = article_creation_workspace_assets(context_object)
                if filename not in workspace_assets:
                    workspace_assets.append(filename)
                context_object.image_assets = workspace_assets
                context_object.is_dirty = True
                context_object.save(update_fields=["image_assets", "is_dirty", "updated_at"])
            elif context_kind == "edit_workspace":
                workspace_assets = article_edit_workspace_assets(context_object)
                owned_assets = article_edit_workspace_owned_assets(context_object)
                if filename not in workspace_assets:
                    workspace_assets.append(filename)
                if filename not in owned_assets:
                    owned_assets.append(filename)
                context_object.image_assets = workspace_assets
                context_object.owned_image_assets = owned_assets
                # Existing-article image uploads are temporary editor state. They
                # must not silently turn the workspace into a restorable draft;
                # only the explicit Save draft action may set is_dirty=True.
                context_object.save(update_fields=[
                    "image_assets", "owned_image_assets", "updated_at"
                ])
    except Exception:
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass
        raise

    log_activity(
        request,
        ActivityLog.EventType.IMAGE_UPLOADED,
        details={
            "filename": filename,
            "original_name": image_log.original_name,
            "content_type": image_log.content_type,
            "size_bytes": image_log.size_bytes,
            "editor_context": context_kind,
            "workspace_id": str(context_object.pk) if context_kind == "workspace" else "",
            "edit_workspace_id": str(context_object.pk) if context_kind == "edit_workspace" else "",
            "article_id": (
                context_object.article_id
                if context_kind == "edit_workspace"
                else context_object.pk if context_kind == "article" else None
            ),
        },
    )

    if filename not in active_pending_uploads:
        active_pending_uploads.append(filename)
    request.session["pending_article_uploads"] = active_pending_uploads[-max(article_limit, 1):]
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
    """Delete an uncommitted image from its authorised editor context."""
    filename = (request.POST.get("filename") or "").strip()
    if not filename:
        return JsonResponse({"error": _("No image filename received.")}, status=400)
    if len(filename) > FILENAME_MAX_LENGTH:
        return JsonResponse({"error": _("Invalid image filename.")}, status=400)
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        return JsonResponse({"error": _("Invalid image filename.")}, status=400)

    context_kind, context_object = _resolve_article_image_editor_context(request)
    pending_uploads = request.session.get("pending_article_uploads", [])

    with transaction.atomic():
        physical_delete_allowed = True
        if context_kind == "workspace":
            context_object = get_owned_article_creation_workspace(
                request.user,
                context_object.pk,
                for_update=True,
            )
            if context_object is None:
                return JsonResponse({"error": _("Article workspace not found.")}, status=404)
            workspace_assets = article_creation_workspace_assets(context_object)
            if filename not in workspace_assets:
                return JsonResponse({"error": _("This image does not belong to this article workspace.")}, status=403)
            if not user_owns_pending_article_image(request.user, filename):
                return JsonResponse({"error": _("This pending image does not belong to your account or is already used by an article.")}, status=403)
        elif context_kind == "edit_workspace":
            context_object = get_owned_article_edit_workspace(
                request.user,
                context_object.pk,
                for_update=True,
            )
            if context_object is None:
                return JsonResponse({"error": _("Article edit checkpoint not found.")}, status=404)
            workspace_assets = article_edit_workspace_assets(context_object)
            owned_assets = article_edit_workspace_owned_assets(context_object)
            if filename not in workspace_assets:
                return JsonResponse({"error": _("This image is not part of this edit checkpoint.")}, status=403)
            # If this file belongs to an explicitly saved personal draft, leave
            # that saved snapshot intact until the user presses Save draft again.
            # Removing the image in the current browser is still allowed, but
            # leaving the page without saving restores the last deliberate draft.
            preserve_saved_draft_image = bool(
                context_object.is_dirty
                and filename in extract_article_image_filenames(context_object.body)
            )
            physical_delete_allowed = filename in owned_assets and not preserve_saved_draft_image
            if filename in owned_assets and not user_owns_pending_article_image(request.user, filename):
                return JsonResponse({"error": _("This pending image does not belong to your account or is already used by an article.")}, status=403)
        else:
            if not user_owns_pending_article_image(request.user, filename):
                return JsonResponse({"error": _("This pending image does not belong to your account or is already used by an article.")}, status=403)

        upload_dir = get_openkb_uploads_dir().resolve()
        file_path = (upload_dir / filename).resolve()
        try:
            file_path.relative_to(upload_dir)
        except ValueError:
            return JsonResponse({"error": _("Invalid image path.")}, status=400)

        if physical_delete_allowed and file_path.exists() and file_path.is_file():
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
                    "editor_context": context_kind,
                },
            )

        if context_kind == "workspace":
            context_object.image_assets = [item for item in workspace_assets if item != filename]
            submitted_body = request.POST.get("frm_kb_body")
            if submitted_body is not None:
                try:
                    context_object.body = validate_article_body(submitted_body)
                except ValidationError:
                    pass
            context_object.is_dirty = True
            context_object.save(update_fields=["body", "image_assets", "is_dirty", "updated_at"])
        elif context_kind == "edit_workspace":
            if not preserve_saved_draft_image:
                context_object.image_assets = [item for item in workspace_assets if item != filename]
                if physical_delete_allowed:
                    context_object.owned_image_assets = [
                        item for item in owned_assets if item != filename
                    ]
                # Do not copy the browser's current body into the workspace here.
                # Existing-article text remains manual-save only.
                context_object.save(update_fields=[
                    "image_assets", "owned_image_assets", "updated_at"
                ])

    request.session["pending_article_uploads"] = [item for item in pending_uploads if item != filename]
    request.session.modified = True
    return JsonResponse({"deleted": True, "physical_deleted": physical_delete_allowed})


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
        allowed = user_owns_pending_article_image(request.user, filename)

    if not allowed:
        raise Http404("Image not found")

    return FileResponse(file_path.open("rb"), content_type=uploaded_image_content_type(filename))
