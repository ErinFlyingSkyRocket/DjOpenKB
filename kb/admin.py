from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import quote
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.admin import GroupAdmin as DefaultGroupAdmin, UserAdmin as DefaultUserAdmin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from .models import ActivityLog, AdminActivityLog, ArticleImageUploadLog, ArticleVote, AuthActivityLog, AuthLockoutPolicyStage, SuggestedArticle, SiteSetting, UserMFADevice, UserProfile
from .auth_monitoring import format_retry_after, log_auth_event, reset_user_auth_lockouts
from .admin_audit import (
    build_admin_change_entries,
    build_admin_object_snapshot,
    describe_admin_change_entries,
    log_admin_activity,
)
from .mfa import admin_reset_user_mfa, mfa_status_label
from .views import delete_article_files, log_activity, write_article_files
from .views.services import ensure_article_filename
from .permissions import (
    PERM_ADD_ARTICLES,
    PERM_ADD_INTERNAL_ARTICLES,
    PERM_MANAGE_ARTICLES,
    PERM_MANAGE_INTERNAL_ARTICLES,
    PERM_DELETE_ARTICLES,
    PERM_DELETE_INTERNAL_ARTICLES,
    PERM_USE_ADMIN_TOOLS,
    PERM_VIEW_ARTICLES,
    PERM_VIEW_INTERNAL_ARTICLES,
    PERMISSION_LABELS,
    ROLE_ADMIN_USERS,
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ARTICLE_WRITER,
    ROLE_DEFINITIONS,
    ROLE_GROUP_NAMES,
    ROLE_DISABLED_USER,
    ROLE_REGULAR_USER,
    role_group_summary,
    assign_single_role_group,
    assign_default_kb_role_group,
    enforce_disabled_user_exclusive,
    enforce_admin_users_exclusive,
    enforce_manager_role_precedence,
    enforce_regular_user_default_only,
    highest_role_group_name,
    role_permissions_summary,
    set_user_direct_kb_permission,
    sync_user_staff_flags_from_roles,
    user_has_direct_kb_permission,
    user_has_disabled_role,
    user_can_use_admin_tools,
)


User = get_user_model()



def _set_admin_model_label(model, singular, plural):
    """Translate custom Django Admin model names at runtime without migrations."""
    model._meta.verbose_name = _(singular)
    model._meta.verbose_name_plural = _(plural)


def _set_admin_field_label(model, field_name, label, help_text=None):
    """Translate custom Django Admin field labels at runtime without migrations."""
    try:
        field = model._meta.get_field(field_name)
    except Exception:
        return
    field.verbose_name = _(label)
    if help_text is not None:
        field.help_text = _(help_text)


def _apply_admin_translation_labels():
    """Keep admin labels translatable without altering database schema."""
    _set_admin_model_label(UserProfile, "Main Site User Profile", "Main Site User Profiles")
    _set_admin_model_label(UserMFADevice, "User MFA device", "User MFA devices")
    _set_admin_model_label(AuthActivityLog, "Authentication activity log", "Authentication activity logs")
    _set_admin_model_label(AuthLockoutPolicyStage, "Authentication lockout policy stage", "Authentication lockout policy stages")
    _set_admin_model_label(SuggestedArticle, "Suggested Article", "Suggested Articles")
    _set_admin_model_label(ArticleVote, "Article vote", "Article votes")
    _set_admin_model_label(SiteSetting, "Site setting", "Site settings")
    _set_admin_model_label(ArticleImageUploadLog, "Article image upload log", "Article image upload logs")
    _set_admin_model_label(ActivityLog, "Activity log", "Activity logs")
    _set_admin_model_label(AdminActivityLog, "Admin activity log", "Admin activity logs")

    labels = {
        UserProfile: {
            "user": "User",
            "account_type": "Account Type",
            "auth_source": "Source",
            "can_access_main_site": "Legacy main-site access",
            "preferred_language": "Preferred language",
            "notes": "Notes",
            "created_at": "Created at",
            "updated_at": "Updated at",
        },
        UserMFADevice: {
            "user": "User",
            "secret": "Authenticator key",
            "confirmed": "Confirmed",
            "created_at": "Created at",
            "confirmed_at": "Confirmed at",
            "last_verified_at": "Last verified at",
            "reset_at": "Reset at",
        },
        AuthActivityLog: {
            "created_at": "Created at",
            "event_type": "Event type",
            "success": "Success",
            "user": "User",
            "username": "Username",
            "login_mode": "Login mode",
            "ip_address": "IP address",
            "user_agent": "User agent",
            "path": "Path",
            "request_method": "Request method",
            "details": "Details",
        },
        AdminActivityLog: {
            "created_at": "Created at",
            "event_type": "Event type",
            "admin_user": "Admin user",
            "admin_username": "Admin username",
            "target_app_label": "Target app",
            "target_model": "Target model",
            "target_object_id": "Target object ID",
            "target_repr": "Target object",
            "action_flag": "Django admin action flag",
            "ip_address": "IP address",
            "user_agent": "User agent",
            "path": "Path",
            "request_method": "Request method",
            "status_code": "Status code",
            "change_message": "Change message",
            "details": "Details",
        },
        ActivityLog: {
            "created_at": "Created at",
            "event_type": "Event type",
            "user": "User",
            "username": "Username",
            "article": "Article",
            "article_title": "Article title",
            "article_status": "Article status",
            "article_owner_user_id_snapshot": "Article owner user ID",
            "article_owner_username_snapshot": "Article owner username",
            "article_owner_name_snapshot": "Article owner name",
            "article_owner_email_snapshot": "Article owner email",
            "article_owner_account_type_snapshot": "Article owner account type",
            "ip_address": "IP address",
            "user_agent": "User agent",
            "path": "Path",
            "request_method": "Request method",
            "details": "Details",
        },
        SuggestedArticle: {
            "owner": "Owner",
            "author_username_snapshot": "Author username snapshot",
            "author_name_snapshot": "Author name snapshot",
            "author_email_snapshot": "Author email snapshot",
            "author_account_type_snapshot": "Author account type snapshot",
            "title": "Article title",
            "body": "Article body",
            "keywords": "Keywords",
            "visibility": "Article visibility",
            "status": "Status",
            "approved_by": "Approved by",
            "approved_at": "Approved at",
            "review_notes": "Review notes",
            "review_notes_history": "Review history",
            "pending_update_title": "Pending update title",
            "pending_update_body": "Pending update body",
            "pending_update_keywords": "Pending update keywords",
            "pending_update_image_assets": "Pending update image assets",
            "update_status": "Update status",
            "update_submitted_at": "Update submitted at",
            "update_reviewed_at": "Update reviewed at",
            "view_count": "View count",
            "filename": "Filename",
            "raw_path": "Raw path",
            "wiki_path": "Wiki path",
            "image_assets": "Image assets",
            "deletion_previous_status": "Previous status before deletion queue",
            "deletion_queued_at": "Deletion queued at",
            "deletion_queued_by": "Deletion queued by",
            "deletion_purge_after": "Permanent deletion after",
            "deletion_restored_at": "Deletion restored at",
            "deletion_restored_by": "Deletion restored by",
            "deletion_reason": "Deletion reason",
            "created_at": "Created at",
            "updated_at": "Updated at",
        },
        ArticleVote: {
            "article": "Article",
            "user": "User",
            "value": "Vote",
            "created_at": "Created at",
            "updated_at": "Updated at",
        },
        ArticleImageUploadLog: {
            "filename": "Filename",
            "original_name": "Original name",
            "content_type": "Content type",
            "size_bytes": "Size bytes",
            "uploaded_by": "Uploaded by",
            "uploader_username_snapshot": "Uploader username snapshot",
            "uploader_email_snapshot": "Uploader email snapshot",
            "uploader_account_type_snapshot": "Uploader account type snapshot",
            "upload_ip_address": "Upload IP address",
            "upload_user_agent": "Upload user agent",
            "uploaded_at": "Uploaded at",
            "deleted_at": "Deleted at",
            "deleted_by": "Deleted by",
            "delete_reason": "Delete reason",
        },
        SiteSetting: {
            "stray_upload_cleanup_min_age_minutes": "Stray upload cleanup minimum age (minutes)",
            "article_deletion_queue_retention_days": "Article deletion queue retention (days)",
            "article_image_upload_limit": "Article image upload limit",
            "auth_activity_log_retention_days": "Authentication activity log retention (days)",
            "session_timeout_hours": "User session timeout (hours)",
            "activity_log_retention_days": "General activity log retention (days)",
            "admin_log_rows_per_page": "Admin log rows per page",
            "admin_allowed_cidrs": "Admin allowed IP ranges",
            "auth_lockout_strike_ttl_seconds": "Authentication lockout escalation memory (seconds)",
            "admin_mfa_idle_timeout_seconds": "Admin MFA idle timeout (seconds)",
            "openkb_ai_prompt_limit_per_24_hours": "OpenKB AI prompts per 24 hours",
            "updated_at": "Updated at",
        },
        AuthLockoutPolicyStage: {
            "site_setting": "Site setting",
            "sort_order": "Stage order",
            "failure_limit": "Failed attempts before block",
            "block_seconds": "Block duration (seconds)",
            "repeat_count": "Repeat count",
            "enabled": "Enabled",
        },
    }

    help_texts = {
        (UserProfile, "account_type"): "Admin/LDAP admin accounts can access Django admin when staff status is enabled.",
        (UserProfile, "auth_source"): "Controls whether the password is managed locally in Knowledge Repository or externally by Active Directory.",
        (UserProfile, "can_access_main_site"): "Legacy compatibility field. Use the built-in Active checkbox on the user account to control whether the user can sign in.",
        (UserProfile, "preferred_language"): "Preferred language for the main wiki user interface.",
        (SiteSetting, "stray_upload_cleanup_min_age_minutes"): "Files newer than this many minutes are ignored by the stray upload cleanup tool. Default is 1440 minutes (24 hours) to avoid deleting images while users are drafting articles. Set to 0 to detect/delete stray uploads immediately.",
        (SiteSetting, "article_deletion_queue_retention_days"): "How long deleted published articles remain recoverable in My Profile → Admin tools → Deletion queue before permanent deletion. Default is 7 days. Set to 0 to permanently delete published articles immediately after MFA confirmation.",
        (SiteSetting, "article_image_upload_limit"): "Maximum number of pasted/uploaded images allowed per article, including draft, pending, published, and pending-update versions. Default is 50. Set to 0 to disable article image uploads.",
        (SiteSetting, "auth_activity_log_retention_days"): "Authentication/MFA monitoring logs older than this many days can be deleted by the cleanup command. Use 0 to keep authentication activity logs indefinitely.",
        (SiteSetting, "session_timeout_hours"): "Authenticated and pending-MFA sessions expire after this many hours from sign-in. Default is 8 hours. Allowed range: 1 to 168 hours (7 days).",
        (SiteSetting, "activity_log_retention_days"): "Article/vote/image/admin-tool/admin-site activity logs older than this many days can be deleted by the cleanup command. Use 0 to keep general and admin activity logs indefinitely.",
        (SiteSetting, "admin_log_rows_per_page"): "Number of rows to show per page in Django Admin log tables. Recommended range: 50 to 500. Default is 200.",
        (SiteSetting, "admin_allowed_cidrs"): "Comma or newline separated CIDR/IP allowlist for Django Admin access. Default allows 10.65.0.0/16 and local loopback. Users outside this range receive 404 even if they know the admin URL. Nginx may also enforce a separate outer allowlist in nginx/nginx.conf.",
        (SiteSetting, "auth_lockout_strike_ttl_seconds"): "How long failed-login/MFA escalation history is remembered without a successful login. Successful verification clears it immediately. Default is 604800 seconds (7 days).",
        (SiteSetting, "openkb_ai_prompt_limit_per_24_hours"): "Maximum accepted Ask OpenKB AI questions per user in a fixed 24-hour window. The first accepted question starts the window and later questions do not extend it. Default: 20.",
        (AuthLockoutPolicyStage, "sort_order"): "Lower numbers run first. Use 10, 20, 30, etc. so you can insert stages later.",
        (AuthLockoutPolicyStage, "failure_limit"): "Number of wrong password/MFA attempts required before this stage blocks the user.",
        (AuthLockoutPolicyStage, "block_seconds"): "How long the login/MFA check is blocked after this stage triggers.",
        (AuthLockoutPolicyStage, "repeat_count"): "How many lockouts should use this stage before moving to the next stage. Use 0 on the final stage to repeat forever.",
    }

    for model, field_labels in labels.items():
        for field_name, label in field_labels.items():
            _set_admin_field_label(model, field_name, label, help_texts.get((model, field_name)))


_apply_admin_translation_labels()




def format_admin_duration(seconds):
    """Return a readable duration for Django Admin helper displays."""
    try:
        seconds = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        seconds = 0

    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"

    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"

    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"

    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''} {remaining_seconds} second{'s' if remaining_seconds != 1 else ''}"


def format_admin_duration_with_seconds(seconds):
    """Return a readable duration plus exact seconds for admin clarity."""
    readable = format_admin_duration(seconds)
    try:
        seconds_int = int(seconds or 0)
    except (TypeError, ValueError):
        seconds_int = 0
    return f"{readable} ({seconds_int} seconds)"


def can_modify_django_admin(request):
    """Return True only for full Django Admin superusers.

    Admin Users are synchronised to ``is_staff=True`` and ``is_superuser=True``.
    There is no separate limited Django Admin role anymore.
    """
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_superuser)


class AdminAuditMixin:
    """Add readable AdminActivityLog entries for admin add/change/delete actions.

    Django Admin access itself is restricted to superusers. This mixin focuses
    only on audit capture, not on providing a separate limited admin mode.
    """

    def _admin_audit_snapshot_key(self, obj):
        if obj is None or not getattr(obj, "pk", None):
            return None
        return f"{obj._meta.label_lower}:{obj.pk}"

    def get_admin_audit_extra_snapshot(self, obj):
        """Subclasses can add safe related-object state to field-level audit diffs."""
        return {}

    def get_admin_audit_snapshot(self, obj):
        return build_admin_object_snapshot(obj, extra=self.get_admin_audit_extra_snapshot(obj))

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = getattr(form, "instance", None)
        if obj is None:
            return

        try:
            key = self._admin_audit_snapshot_key(obj)
            before = None
            if change and key:
                before = getattr(request, "_admin_audit_before_snapshots", {}).get(key)
            after = self.get_admin_audit_snapshot(obj)
            target_label = str(obj._meta.verbose_name)
            target_display = str(obj)

            if change:
                entries = build_admin_change_entries(before, after)
                if not entries:
                    return
                change_text = describe_admin_change_entries(entries)
                action_label = _("Changed %(target_label)s %(target)s: %(changes)s") % {
                    "target_label": target_label,
                    "target": target_display,
                    "changes": change_text,
                }
                details = {
                    "source": "admin_field_diff",
                    "action_label": str(action_label),
                    "changed_fields": entries,
                }
                event_type = AdminActivityLog.EventType.ADMIN_CHANGE
            else:
                # For newly-created objects, include the safe initial values so
                # the audit row is useful without opening Django's raw LogEntry.
                created_values = []
                for key_name, item in after.items():
                    value = item.get("value")
                    if value in ("", "-", [], None):
                        continue
                    created_values.append({
                        "field": key_name,
                        "label": item.get("label") or key_name,
                        "value": value,
                        "kind": item.get("kind") or "field",
                    })
                preview_parts = []
                for item in created_values[:8]:
                    value = item.get("value")
                    if isinstance(value, list):
                        value = ", ".join(value[:8])
                    preview_parts.append(f"{item.get('label')}: {value}")
                preview = "; ".join(preview_parts)
                action_label = _("Created %(target_label)s %(target)s") % {
                    "target_label": target_label,
                    "target": target_display,
                }
                if preview:
                    action_label = f"{action_label}: {preview}"
                details = {
                    "source": "admin_field_diff",
                    "action_label": str(action_label),
                    "created_values": created_values,
                }
                event_type = AdminActivityLog.EventType.ADMIN_ADD

            log_admin_activity(
                request=request,
                event_type=event_type,
                target_app_label=obj._meta.app_label,
                target_model=obj._meta.model_name,
                target_object_id=str(obj.pk or ""),
                target_repr=target_display,
                change_message=str(action_label),
                details=details,
            )
        except Exception:
            pass


    def delete_model(self, request, obj):
        target_display = str(obj)
        target_label = str(obj._meta.verbose_name)
        target_app_label = obj._meta.app_label
        target_model = obj._meta.model_name
        target_object_id = str(obj.pk or "")
        super().delete_model(request, obj)
        _log_admin_explicit_action(
            request,
            action_label=_("Deleted %(target_label)s %(target)s") % {"target_label": target_label, "target": target_display},
            target_obj=obj,
            details={
                "source": "admin_delete_model",
                "target_label": target_label,
                "target_display": target_display,
                "deleted_object_id": target_object_id,
            },
            event_type=AdminActivityLog.EventType.ADMIN_DELETE,
        )

    def delete_queryset(self, request, queryset):
        model = queryset.model
        target_label = str(model._meta.verbose_name_plural)
        preview = [str(obj)[:120] for obj in queryset[:20]]
        count = queryset.count()
        super().delete_queryset(request, queryset)
        _log_admin_explicit_action(
            request,
            action_label=_("Deleted %(count)d %(target_label)s: %(preview)s") % {
                "count": count,
                "target_label": target_label,
                "preview": ", ".join(preview[:5]) + (f", +{count - 5} more" if count > 5 else ""),
            },
            details={
                "source": "admin_delete_queryset",
                "target_label": target_label,
                "selected_count": count,
                "selected_objects_preview": preview,
            },
            event_type=AdminActivityLog.EventType.ADMIN_DELETE,
        )


def require_admin_reset_permission(request):
    """Allow custom admin reset actions only for full administrators."""
    if not user_can_use_admin_tools(request.user):
        raise PermissionDenied(_("You do not have permission to perform this admin reset."))


def _log_admin_explicit_action(request, *, action_label, target_obj=None, details=None, event_type=None):
    """Write a clear AdminActivityLog row for custom admin buttons/actions."""
    details = {**(details or {})}
    details.setdefault("source", "explicit_admin_action")
    details["action_label"] = str(action_label)

    target_app_label = ""
    target_model = ""
    target_object_id = ""
    target_repr = ""
    if target_obj is not None:
        try:
            target_app_label = target_obj._meta.app_label
            target_model = target_obj._meta.model_name
            target_object_id = str(target_obj.pk or "")
            target_repr = str(target_obj)
            details.setdefault("target_display", target_repr)
        except Exception:
            target_repr = str(target_obj)
            details.setdefault("target_display", target_repr)

    log_admin_activity(
        request=request,
        event_type=event_type or AdminActivityLog.EventType.ADMIN_ACTION,
        target_app_label=target_app_label,
        target_model=target_model,
        target_object_id=target_object_id,
        target_repr=target_repr,
        change_message=str(action_label),
        details=details,
    )

def get_admin_log_rows_per_page():
    """Return admin log row count from Site settings with safe bounds."""
    try:
        value = int(SiteSetting.load().admin_log_rows_per_page or 200)
    except Exception:
        return 200

    if value < 25:
        return 25
    if value > 500:
        return 500
    return value


class SiteSettingLogPaginationMixin:
    """Use Site settings to control log rows per page in Django Admin."""

    list_per_page = 200
    list_max_show_all = 500

    def changelist_view(self, request, extra_context=None):
        rows_per_page = get_admin_log_rows_per_page()
        self.list_per_page = rows_per_page
        self.list_max_show_all = max(rows_per_page, 500)
        return super().changelist_view(request, extra_context=extra_context)

DIRECT_PERMISSION_FIELD_MAP = {
    "direct_can_view_articles": PERM_VIEW_ARTICLES,
    "direct_can_add_articles": PERM_ADD_ARTICLES,
    "direct_can_manage_articles": PERM_MANAGE_ARTICLES,
    "direct_can_delete_articles": PERM_DELETE_ARTICLES,
    "direct_can_view_internal_articles": PERM_VIEW_INTERNAL_ARTICLES,
    "direct_can_add_internal_articles": PERM_ADD_INTERNAL_ARTICLES,
    "direct_can_manage_internal_articles": PERM_MANAGE_INTERNAL_ARTICLES,
    "direct_can_delete_internal_articles": PERM_DELETE_INTERNAL_ARTICLES,
}


class UserProfileAccountFormMixin:
    """Validate editable account type/source combinations in Django Admin."""

    def _setup_account_type_help(self):
        if "account_type" in self.fields:
            self.fields["account_type"].help_text = _(
                "Choose whether this profile is a local user/admin or an LDAP user/admin. "
                "Admin account types add the Admin Users role and become Django superusers. "
                "Changing back to User/LDAP user removes Admin Users and clears staff/superuser status. "
                "Use this to convert an abandoned LDAP profile to a local profile when the AD account is deleted but articles must be retained."
            )
        if "auth_source" in self.fields:
            self.fields["auth_source"].help_text = _(
                "Must match Account Type. Local User/Local Admin use Local user. "
                "LDAP user/LDAP admin use Active Directory user. "
                "To convert an LDAP account to a local account, set Account Type to User or Admin and Source to Local user, then set a local password."
            )

    def clean(self):
        cleaned_data = super().clean()
        account_type = cleaned_data.get("account_type")
        auth_source = cleaned_data.get("auth_source")
        if not account_type or not auth_source:
            return cleaned_data

        expected_source = UserProfile.expected_auth_source_for_account_type(account_type)
        if auth_source != expected_source:
            if expected_source == UserProfile.AuthSource.AD:
                self.add_error(
                    "auth_source",
                    _(
                        "LDAP account types must use Active Directory user as the source. "
                        "Choose LDAP user/LDAP admin with Active Directory user, or choose User/Admin with Local user."
                    ),
                )
            else:
                self.add_error(
                    "auth_source",
                    _(
                        "Local account types must use Local user as the source. "
                        "Choose User/Admin with Local user, or choose LDAP user/LDAP admin with Active Directory user."
                    ),
                )
        return cleaned_data


class UserProfileInlineForm(UserProfileAccountFormMixin, forms.ModelForm):
    """Expose account recovery/source settings and direct permissions in User admin.

    Groups remain the standard role templates. These checkboxes add/remove only
    direct user permissions, so admins can make exceptions without creating a
    custom group for every special case.
    """

    direct_can_view_articles = forms.BooleanField(
        required=False,
        label=_("Can view articles"),
        help_text=_(
            "Direct user permission. Allows the user to open the main wiki and read published articles after login. "
            "It does not allow creating articles, approving articles, or using admin tools. "
            "Unticked means no direct user grant; the user may still receive this permission from their group. "
            "The Disabled User group overrides direct permission add-ons."
        ),
    )
    direct_can_add_articles = forms.BooleanField(
        required=False,
        label=_("Can create articles"),
        help_text=_(
            "Direct user permission. Allows the user to create article drafts, submit new articles for approval, "
            "and edit/resubmit their own articles or pending updates. It does not allow approving/rejecting other users' articles "
            "or using Django admin tools."
        ),
    )
    direct_can_manage_articles = forms.BooleanField(
        required=False,
        label=_("Can approve/manage pending articles"),
        help_text=_(
            "Direct user permission. Allows access to article approval workflows such as pending article review, "
            "pending update review, approve/reject actions, and review-stage article editing. "
            "It does not automatically allow creating new articles, deleting articles, or full Django admin access unless another permission/group grants those."
        ),
    )
    direct_can_delete_articles = forms.BooleanField(
        required=False,
        label=_("Can delete articles"),
        help_text=_(
            "Direct user permission. Allows this user to delete articles. Use sparingly; the standard Article Manager and Admin Users groups already grant this."
        ),
    )

    direct_can_view_internal_articles = forms.BooleanField(
        required=False,
        label=_("Can view internal articles"),
        help_text=_("Direct user permission. Allows this user to open and read internal/private published articles."),
    )
    direct_can_add_internal_articles = forms.BooleanField(
        required=False,
        label=_("Can create internal articles"),
        help_text=_("Direct user permission. Allows this user to create and submit internal/private articles."),
    )
    direct_can_manage_internal_articles = forms.BooleanField(
        required=False,
        label=_("Can approve/manage internal articles"),
        help_text=_("Direct user permission. Allows this user to review and manage pending internal/private articles."),
    )
    direct_can_delete_internal_articles = forms.BooleanField(
        required=False,
        label=_("Can delete internal articles"),
        help_text=_("Direct user permission. Allows this user to delete internal/private articles."),
    )
    class Meta:
        model = UserProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_account_type_help()
        user = getattr(self.instance, "user", None)
        if not user or not getattr(user, "pk", None):
            return

        for field_name, codename in DIRECT_PERMISSION_FIELD_MAP.items():
            self.fields[field_name].initial = user_has_direct_kb_permission(user, codename)

    def save(self, commit=True):
        profile = super().save(commit=commit)

        if commit and getattr(profile, "user_id", None) and hasattr(self, "cleaned_data"):
            for field_name, codename in DIRECT_PERMISSION_FIELD_MAP.items():
                set_user_direct_kb_permission(
                    profile.user,
                    codename,
                    bool(self.cleaned_data.get(field_name)),
                )
            if enforce_disabled_user_exclusive(profile.user):
                return profile
            enforce_admin_users_exclusive(profile.user)
            enforce_manager_role_precedence(profile.user)
            sync_user_staff_flags_from_roles(profile.user)

        return profile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    form = UserProfileInlineForm
    can_delete = False
    extra = 0
    fieldsets = (
        (
            _("Main site profile"),
            {
                "fields": (
                    "account_type",
                    "auth_source",
                    "preferred_language",
                    "notes",
                )
            },
        ),
        (
            _("Knowledge Repository role permissions"),
            {
                "fields": (
                    "permission_exception_guide",
                    "effective_role_group",
                    "effective_permissions",
                    "direct_can_view_articles",
                    "direct_can_add_articles",
                    "direct_can_manage_articles",
                    "direct_can_delete_articles",
                ),
                "description": _(
                    "Use Groups as the user's standard public-article role. Tick these boxes only when this specific user needs extra public-article permissions "
                    "outside their group. The Disabled User group overrides these direct permission add-ons."
                ),
            },
        ),
        (
            _("Internal article permissions"),
            {
                "fields": (
                    "direct_can_view_internal_articles",
                    "direct_can_add_internal_articles",
                    "direct_can_manage_internal_articles",
                    "direct_can_delete_internal_articles",
                ),
                "description": _(
                    "Internal roles are add-on permissions. An internal user can still view general/public articles. "
                    "Use the Internal User / Internal Article Writer / Internal Article Approver / Internal Article Manager groups whenever possible; tick these direct boxes only for exceptions."
                ),
            },
        ),
        (
            _("Timestamps"),
            {"fields": ("created_at", "updated_at")},
        ),
    )
    readonly_fields = (
        "permission_exception_guide",
        "effective_role_group",
        "effective_permissions",
        "created_at",
        "updated_at",
    )

    def permission_exception_guide(self, obj):
        rows = (
            (
                _("Group permissions"),
                _(
                    "Set the user's normal role from the Groups section. Custom non-role groups may be used later for things like notifications."
                ),
            ),
            (
                _("Disabled User"),
                _(
                    "Highest precedence. The user is redirected to the account-disabled page and cannot access Knowledge Repository. "
                    "It also clears Admin Users, staff/superuser status, and direct Knowledge Repository permission add-ons."
                ),
            ),
            (
                _("Regular User"),
                _("Can sign in, view/search published articles, use normal article pages, and vote where voting is enabled."),
            ),
            (
                _("Article Writer"),
                _(
                    "Can create article drafts, submit new articles for approval, and edit/resubmit their own articles. "
                    "They cannot approve or reject articles."
                ),
            ),
            (
                _("Article Approver"),
                _(
                    "Can review pending articles/updates, edit content during pending review, and approve or reject them. Cannot edit already-published articles or delete articles by default. "
                    "Article Approvers cannot add new articles or delete articles by default."
                ),
            ),
            (
                _("Article Manager"),
                _(
                    "Can create articles, edit/manage articles, review pending articles/updates, approve/reject submissions, and delete articles."
                ),
            ),
            (
                _("Internal User"),
                _("Add-on role. Can view public/general articles and internal articles, but cannot create or approve internal articles."),
            ),
            (
                _("Internal Article Writer"),
                _("Add-on role. Can view public/general articles and create/edit their own internal articles."),
            ),
            (
                _("Internal Article Approver"),
                _("Add-on role. Can view public/general articles and approve/reject internal pending articles and updates."),
            ),
            (
                _("Internal Article Manager"),
                _("Add-on role. Can view public/general articles, create/manage/delete internal articles, and review internal pending articles."),
            ),
            (
                _("Admin Users"),
                _(
                    "Full Django Admin superuser access. Admin Users require the extra admin MFA verification before entering Django Admin."
                ),
            ),
            (
                _("Direct user permissions"),
                _(
                    "The checkboxes below are special per-user exceptions for article access only. They are useful when one user needs "
                    "slightly more article access than their group, without creating a new group. Django Admin access is granted only through the Admin Users group/superuser status."
                ),
            ),
            (
                _("Unticking a checkbox"),
                _(
                    "This removes only the direct user permission. If the user's group still grants the same permission, "
                    "the effective permission will remain active."
                ),
            ),
            (
                _("Effective permissions"),
                _(
                    "This line shows the final result after Django combines group permissions, direct user permissions, "
                    "and superuser status."
                ),
            ),
        )
        return format_html(
            "<div style='max-width:920px;line-height:1.5;'>"
            "<p class='help'><strong>{}</strong> {}</p>"
            "<table style='border-collapse:collapse;margin-top:8px;'>"
            "{}"
            "</table>"
            "<p class='help' style='margin-top:8px;'>{}</p>"
            "</div>",
            _("How to use these permissions:"),
            _(
                "Choose a group first, then use direct checkboxes only for exceptions. "
                "For most users, the group alone should be enough."
            ),
            format_html_join(
                "",
                "<tr>"
                "<th style='text-align:left;vertical-align:top;padding:4px 12px 4px 0;white-space:nowrap;'>{}</th>"
                "<td style='padding:4px 0;'>{}</td>"
                "</tr>",
                rows,
            ),
            _(
                "After saving, re-open the user if needed and check 'Effective permissions' to confirm the final access level."
            ),
        )

    permission_exception_guide.short_description = _("Permission guide")

    def effective_role_group(self, obj):
        if not obj or not getattr(obj, "user_id", None):
            return "-"
        return role_group_summary(obj.user)

    effective_role_group.short_description = _("Effective role groups")

    def effective_permissions(self, obj):
        if not obj or not getattr(obj, "user_id", None):
            return "-"
        return role_permissions_summary(obj.user)

    effective_permissions.short_description = _("Effective permissions")


class GroupAdminForm(forms.ModelForm):
    """Use a left/right searchable selector to manage users inside a group."""

    group_users = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        label=_("Users in this group"),
        widget=FilteredSelectMultiple(_("users"), is_stacked=False),
        help_text=_(
            "Select the users who should belong to this group. Use the filter box on the left to search users, "
            "move selected users to the right, then save. Removing a user from the right removes only this group membership."
        ),
    )

    class Meta:
        model = Group
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group_users"].queryset = User.objects.order_by("username", "email")
        self._original_group_user_ids = set()
        if self.instance and self.instance.pk:
            current_users = self.instance.user_set.all()
            self.fields["group_users"].initial = current_users
            self._original_group_user_ids = set(current_users.values_list("pk", flat=True))

    def save_m2m(self):
        super().save_m2m()
        if self.instance and self.instance.pk and "group_users" in self.cleaned_data:
            selected_users = self.cleaned_data["group_users"]
            selected_user_ids = set(selected_users.values_list("pk", flat=True))
            affected_user_ids = self._original_group_user_ids | selected_user_ids

            self.instance.user_set.set(selected_users)

            for user in User.objects.filter(pk__in=affected_user_ids):
                if enforce_disabled_user_exclusive(user):
                    continue
                enforce_admin_users_exclusive(user)
                enforce_manager_role_precedence(user)
                enforce_regular_user_default_only(user)
                assign_default_kb_role_group(user)
                sync_user_staff_flags_from_roles(user)


try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(AdminAuditMixin, DefaultGroupAdmin):
    """Make group membership manageable directly from the Group admin page."""

    form = GroupAdminForm
    list_display = ("name", "group_type", "member_count", "member_preview", "role_permissions")
    search_fields = (
        "name",
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
    )
    list_filter = ("permissions__content_type__app_label",)
    filter_horizontal = ("permissions",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("user_set", "permissions")

    def get_admin_audit_extra_snapshot(self, obj):
        if not obj or not getattr(obj, "pk", None):
            return {}
        return {
            "group_users": {
                "label": str(_("Users in this group")),
                "value": sorted(user.get_username() for user in obj.user_set.all()),
                "kind": "m2m",
            },
        }

    def get_fieldsets(self, request, obj=None):
        role_fields = ("name", "permissions")
        if obj and obj.name in ROLE_GROUP_NAMES:
            # Protected role permissions are seeded by the app and not editable
            # from Django Admin. Membership remains editable below.
            role_fields = ("name", "djopenkb_role_guide")

        return (
            (None, {"fields": role_fields}),
            (
                _("Group members"),
                {
                    "fields": ("group_users",),
                    "description": _(
                        "Use this searchable left/right list to add or remove users from this group. "
                        "Users on the right are members of this group. Users on the left are available users."
                    ),
                },
            ),
        )

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.name in ROLE_GROUP_NAMES:
            for field_name in ("name", "djopenkb_role_guide"):
                if field_name not in readonly_fields:
                    readonly_fields.append(field_name)
        return tuple(readonly_fields)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.name in ROLE_GROUP_NAMES:
            return False
        return super().has_delete_permission(request, obj=obj)

    def delete_model(self, request, obj):
        if obj.name in ROLE_GROUP_NAMES:
            self.message_user(
                request,
                _("Default Knowledge Repository role groups cannot be deleted."),
                level=messages.ERROR,
            )
            return
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        protected_count = queryset.filter(name__in=ROLE_GROUP_NAMES).count()
        if protected_count:
            self.message_user(
                request,
                _("Default Knowledge Repository role groups cannot be deleted. %(count)d protected group(s) were skipped.")
                % {"count": protected_count},
                level=messages.ERROR,
            )
            queryset = queryset.exclude(name__in=ROLE_GROUP_NAMES)
            if not queryset.exists():
                return
        return super().delete_queryset(request, queryset)

    def djopenkb_role_guide(self, obj):
        if not obj or obj.name not in ROLE_DEFINITIONS:
            return "-"

        definition = ROLE_DEFINITIONS[obj.name]
        permissions = definition.get("permissions", ())
        permission_labels = [str(_(PERMISSION_LABELS.get(codename, codename))) for codename in permissions]
        permission_text = ", ".join(permission_labels) if permission_labels else str(_("No Knowledge Repository role permissions"))

        return format_html(
            "<div style='max-width:920px;line-height:1.5;'>"
            "<p><strong>{}</strong></p>"
            "<p>{}</p>"
            "<p><strong>{}</strong> {}</p>"
            "<p class='help'>{}</p>"
            "</div>",
            _("Knowledge Repository role group"),
            definition.get("description", ""),
            _("Default permissions:"),
            permission_text,
            _(
                "Use the Group members selector below to add or remove users from this role. "
                "For one-off exceptions, edit the specific user and tick the direct Knowledge Repository permission checkboxes instead. "
                "New non-admin users are automatically placed into Regular User when their account is created."
            ),
        )

    djopenkb_role_guide.short_description = _("Knowledge Repository role information")

    def group_type(self, obj):
        if obj.name in ROLE_GROUP_NAMES:
            return _("Knowledge Repository role")
        return _("Custom Django group")

    group_type.short_description = _("Group type")

    def member_count(self, obj):
        return obj.user_set.count()

    member_count.short_description = _("Users")

    def member_preview(self, obj):
        users = list(obj.user_set.all()[:8])
        if not users:
            return _("No users")
        names = [user.get_username() for user in users]
        extra = obj.user_set.count() - len(names)
        preview = ", ".join(names)
        if extra > 0:
            preview = _("%(preview)s, +%(extra)d more") % {"preview": preview, "extra": extra}
        return preview

    member_preview.short_description = _("Current users")

    def role_permissions(self, obj):
        if obj.name in ROLE_DEFINITIONS:
            permissions = ROLE_DEFINITIONS[obj.name].get("permissions", ())
            labels = [str(_(PERMISSION_LABELS.get(codename, codename))) for codename in permissions]
            return ", ".join(labels) if labels else _("No Knowledge Repository role permissions")

        permissions = [permission.name for permission in obj.permissions.all()[:6]]
        if not permissions:
            return _("No permissions")
        extra = obj.permissions.count() - len(permissions)
        text = ", ".join(permissions)
        if extra > 0:
            text = _("%(text)s, +%(extra)d more") % {"text": text, "extra": extra}
        return text

    role_permissions.short_description = _("Permissions")


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(AdminAuditMixin, DefaultUserAdmin):
    inlines = (UserProfileInline,)

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_active",
        "is_staff",
        "is_superuser",
        "account_status_display",
        "djopenkb_role_group",
        "djopenkb_permissions",
        "mfa_status_display",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "kb_profile__account_type",
        "kb_profile__auth_source",
        "kb_mfa_device__confirmed",
    )
    search_fields = (
        "username",
        "email",
        "first_name",
        "last_name",
    )
    actions = (
        "set_selected_users_disabled",
        "set_selected_users_regular",
        "set_selected_users_writer",
        "set_selected_users_approver",
        "set_selected_users_manager",
        "set_selected_users_admin",
        "reset_mfa_for_selected_users",
        "reset_auth_lockouts_for_selected_users",
    )

    def _is_domain_user(self, obj):
        """Return True when this Django user is managed by AD/LDAP."""
        profile = getattr(obj, "kb_profile", None)
        return bool(profile and getattr(profile, "is_ad_managed", False))

    def get_admin_audit_extra_snapshot(self, obj):
        if not obj or not getattr(obj, "pk", None):
            return {}

        profile, _ = UserProfile.objects.get_or_create(user=obj)
        direct_permissions = []
        for codename in DIRECT_PERMISSION_FIELD_MAP.values():
            if user_has_direct_kb_permission(obj, codename):
                direct_permissions.append(str(_(PERMISSION_LABELS.get(codename, codename))))

        return {
            "profile_account_type": {
                "label": str(_("Profile account type")),
                "value": profile.get_account_type_display(),
                "kind": "field",
            },
            "profile_auth_source": {
                "label": str(_("Authentication source")),
                "value": profile.get_auth_source_display(),
                "kind": "field",
            },
            "profile_account_status": {
                "label": str(_("Account status")),
                "value": str(self.account_status_display(obj)),
                "kind": "field",
            },
            "profile_preferred_language": {
                "label": str(_("Preferred language")),
                "value": profile.preferred_language or "-",
                "kind": "field",
            },
            "direct_knowledge_repository_permissions": {
                "label": str(_("Direct Knowledge Repository permissions")),
                "value": sorted(direct_permissions),
                "kind": "m2m",
            },
        }

    def domain_password_status(self, obj):
        return _("Domain password is managed in Active Directory and cannot be changed from Django admin.")

    domain_password_status.short_description = _("Password")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and self._is_domain_user(obj) and "domain_password_status" not in readonly_fields:
            readonly_fields.append("domain_password_status")
        if obj:
            for field in ("mfa_status_display", "mfa_reset_button", "auth_lockout_reset_button"):
                if field not in readonly_fields:
                    readonly_fields.append(field)
        return tuple(readonly_fields)

    def _default_group_guide_html(self):
        rows = (
            (_("Disabled User"), _("Highest precedence. Blocks Knowledge Repository access and clears Admin Users, staff/superuser status, and direct role permissions.")),
            (_("Regular User"), _("Can view/search published articles and vote. This is the fallback viewer role and is only auto-added when the user has no other standard Knowledge Repository role.")),
            (_("Article Writer"), _("Can create article drafts, submit articles for approval, and edit/resubmit their own articles. Cannot approve or reject articles. Includes viewer access, so Regular User is not required.")),
            (_("Article Approver"), _("Can review pending articles/updates, edit content during pending review, and approve or reject them. Cannot edit already-published articles or delete articles by default. Cannot add or delete articles by default. Includes viewer access, so Regular User is not required.")),
            (_("Article Manager"), _("Can create articles, edit/manage articles, review pending articles/updates, approve/reject submissions, and delete articles. Includes viewer access, so Regular User is not required.")),
            (_("Internal User"), _("Add-on role. Can view public/general articles plus internal articles.")),
            (_("Internal Article Writer"), _("Add-on role. Can create/edit own internal articles and view public/general articles.")),
            (_("Internal Article Approver"), _("Add-on role. Can review internal pending articles/updates and view public/general articles.")),
            (_("Internal Article Manager"), _("Add-on role. Can create/manage/delete internal articles and view public/general articles.")),
            (_("Admin Users"), _("Full Django Admin superuser access. Requires the extra Admin MFA step before entering Django Admin.")),
        )
        return format_html(
            "<div style='max-width:980px;line-height:1.5;'>"
            "<p class='help'><strong>{}</strong> {}</p>"
            "<table style='border-collapse:collapse;margin-top:8px;margin-bottom:8px;'>{}</table>"
            "<p class='help'>{}</p>"
            "</div>",
            _("Default Knowledge Repository groups:"),
            _("Use these groups as the main access model. Disabled User always takes highest precedence."),
            format_html_join(
                "",
                "<tr><th style='text-align:left;vertical-align:top;padding:4px 12px 4px 0;white-space:nowrap;'>{}</th><td style='padding:4px 0;'>{}</td></tr>",
                rows,
            ),
            _(
                "The Active checkbox controls whether the account can sign in at all. "
                "Regular User is the fallback viewer role only and is removed automatically when Writer, Approver, Manager, or elevated internal roles are assigned. "
                "Internal User is an add-on viewer role and can be combined with Regular User. Custom non-role groups can still be used later for notifications or department grouping."
            ),
        )

    def _fieldset_contains_field(self, fields, field_name):
        if isinstance(fields, str):
            return fields == field_name
        if isinstance(fields, (list, tuple)):
            return any(self._fieldset_contains_field(item, field_name) for item in fields)
        return False

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)

        cleaned_fieldsets = []
        for title, options in fieldsets:
            options = dict(options)
            fields = options.get("fields", ())

            if self._fieldset_contains_field(fields, "groups"):
                options["description"] = self._default_group_guide_html()

            if obj and self._is_domain_user(obj):
                def replace_password_field(value):
                    if value == "password":
                        return "domain_password_status"
                    if isinstance(value, (list, tuple)):
                        replaced = [replace_password_field(item) for item in value]
                        return tuple(item for item in replaced if item)
                    return value

                fields = replace_password_field(fields)

            options["fields"] = fields
            cleaned_fieldsets.append((title, options))

        if obj:
            cleaned_fieldsets.append((
                _("Multi-factor authentication"),
                {
                    "fields": ("mfa_status_display", "mfa_reset_button"),
                    "description": _(
                        "Use this section to reset a user's authenticator setup. "
                        "A reset generates a new private authenticator secret and forces the user to scan a new QR code at next sign-in."
                    ),
                },
            ))
            cleaned_fieldsets.append((
                _("Authentication lockout"),
                {
                    "fields": ("auth_lockout_reset_button",),
                    "description": _(
                        "Use this if the user is temporarily blocked because of repeated wrong password or MFA attempts. "
                        "It clears password and MFA lockout counters for this user only."
                    ),
                },
            ))

        return tuple(cleaned_fieldsets)

    def user_change_password(self, request, id, form_url=""):
        obj = self.get_object(request, id)
        if obj and self._is_domain_user(obj):
            raise Http404(_("Domain user passwords are managed in Active Directory."))
        return super().user_change_password(request, id, form_url)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        profile, created = UserProfile.objects.get_or_create(user=obj)
        if not enforce_disabled_user_exclusive(obj):
            enforce_admin_users_exclusive(obj)
            enforce_manager_role_precedence(obj)
        sync_user_staff_flags_from_roles(obj)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        user = form.instance
        # Run after Django saves groups/user_permissions/inlines so Disabled User
        # cannot be combined with old direct Knowledge Repository permission overrides.
        if enforce_disabled_user_exclusive(user):
            return
        enforce_admin_users_exclusive(user)
        enforce_manager_role_precedence(user)
        enforce_regular_user_default_only(user)
        assign_default_kb_role_group(user)
        sync_user_staff_flags_from_roles(user)

    def account_status_display(self, obj):
        profile = getattr(obj, "kb_profile", None)

        if not obj.is_active:
            return _("Inactive")

        if user_has_disabled_role(obj):
            return _("Disabled User")

        is_ldap = bool(profile and profile.auth_source == UserProfile.AuthSource.AD)
        is_admin = obj.groups.filter(name=ROLE_ADMIN_USERS).exists()

        if is_admin and is_ldap:
            return _("LDAP admin")
        if is_admin:
            return _("Local admin")
        if is_ldap:
            return _("LDAP user")
        return _("Local user")

    account_status_display.short_description = _("Account Status")

    def djopenkb_role_group(self, obj):
        return role_group_summary(obj)

    djopenkb_role_group.short_description = _("Knowledge Repository Roles")

    def djopenkb_permissions(self, obj):
        return role_permissions_summary(obj)

    djopenkb_permissions.short_description = _("Knowledge Repository Permissions")


    def mfa_status_display(self, obj):
        status = mfa_status_label(obj)
        status_text = str(status)
        if status_text == str(_("Configured")):
            return format_html('<span style="color:#0a7a2f;font-weight:600;">{}</span>', status)
        if status_text == str(_("Setup pending")):
            return format_html('<span style="color:#a15c00;font-weight:600;">{}</span>', status)
        return format_html('<span style="color:#8a1f11;font-weight:600;">{}</span>', status)

    mfa_status_display.short_description = _("MFA Status")

    def mfa_reset_button(self, obj):
        if not obj or not obj.pk:
            return "-"
        url = reverse("admin:kb_user_reset_mfa", args=[quote(obj.pk)])
        return format_html(
            '<a class="button" href="{}">{}</a><p class="help">{}</p>',
            url,
            _("Reset MFA"),
            _(
                "Resets this user's authenticator and requires a fresh QR setup. "
                "The new authenticator key is private and is not displayed to admins."
            ),
        )

    mfa_reset_button.short_description = _("MFA Reset")

    def auth_lockout_reset_button(self, obj):
        if not obj or not obj.pk:
            return "-"
        url = reverse("admin:kb_user_reset_auth_lockout", args=[quote(obj.pk)])
        return format_html(
            '<a class="button" href="{}">{}</a><p class="help">{}</p>',
            url,
            _("Reset password/MFA lockout"),
            _(
                "Clears temporary blocks and progressive lockout history for this user. "
                "Use this after verifying the request is legitimate."
            ),
        )

    auth_lockout_reset_button.short_description = _("Authentication Lockout Reset")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:user_id>/reset-mfa/",
                self.admin_site.admin_view(self.reset_user_mfa_view),
                name="kb_user_reset_mfa",
            ),
            path(
                "<path:user_id>/reset-auth-lockout/",
                self.admin_site.admin_view(self.reset_user_auth_lockout_view),
                name="kb_user_reset_auth_lockout",
            ),
        ]
        return custom_urls + urls

    def reset_user_auth_lockout_view(self, request, user_id):
        require_admin_reset_permission(request)
        user = self.get_object(request, user_id)
        if user is None:
            raise Http404(_("User does not exist."))

        opts = self.model._meta
        user_change_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[quote(user.pk)],
        )

        if request.method == "POST":
            identifiers = reset_user_auth_lockouts(user)
            log_auth_event(
                request,
                event_type="auth_lockout_reset_admin",
                success=True,
                user=user,
                username=user.get_username(),
                details={
                    "actor": request.user.get_username(),
                    "reset_identifiers": identifiers,
                    "source": "user_change_button",
                },
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Reset password/MFA lockout for user %(username)s") % {"username": user.get_username()},
                target_obj=user,
                details={"reset_identifiers": identifiers, "admin_action": "reset_user_auth_lockout"},
            )
            self.message_user(
                request,
                _("Password/MFA lockout counters were reset for %(username)s.") % {"username": user.get_username()},
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(user_change_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Reset password/MFA lockout for %(username)s") % {"username": user.get_username()},
            "user_obj": user,
            "user_change_url": user_change_url,
        }
        return TemplateResponse(request, "admin/kb/reset_auth_lockout_confirm.html", context)

    def reset_user_mfa_view(self, request, user_id):
        require_admin_reset_permission(request)
        user = self.get_object(request, user_id)
        if user is None:
            raise Http404(_("User does not exist."))

        opts = self.model._meta
        user_change_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[quote(user.pk)],
        )

        if request.method == "POST":
            _device, sessions_deleted = admin_reset_user_mfa(user)
            log_auth_event(
                request,
                event_type="mfa_reset_admin",
                success=True,
                user=user,
                username=user.get_username(),
                details={"actor": request.user.get_username(), "sessions_deleted": sessions_deleted},
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Reset MFA for user %(username)s") % {"username": user.get_username()},
                target_obj=user,
                details={"sessions_deleted": sessions_deleted, "admin_action": "reset_user_mfa"},
            )
            self.message_user(
                request,
                _(
                    "MFA was reset for %(username)s. The user must set up a new authenticator at next sign-in."
                ) % {"username": user.get_username()},
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(user_change_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Reset MFA for %(username)s") % {"username": user.get_username()},
            "user_obj": user,
            "mfa_status": mfa_status_label(user),
            "user_change_url": user_change_url,
        }
        return TemplateResponse(request, "admin/kb/reset_mfa_confirm.html", context)

    @admin.action(description=_("Reset MFA for selected users"))
    def reset_mfa_for_selected_users(self, request, queryset):
        require_admin_reset_permission(request)
        count = 0
        for user in queryset:
            _device, sessions_deleted = admin_reset_user_mfa(user)
            log_auth_event(
                request,
                event_type="mfa_reset_admin",
                success=True,
                user=user,
                username=user.get_username(),
                details={"actor": request.user.get_username(), "sessions_deleted": sessions_deleted, "source": "bulk_user_action"},
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Reset MFA for user %(username)s") % {"username": user.get_username()},
                target_obj=user,
                details={"sessions_deleted": sessions_deleted, "admin_action": "reset_mfa_for_selected_users"},
            )
            count += 1
        self.message_user(
            request,
            _("MFA reset for %(count)d selected user(s). They must set up a new authenticator at next sign-in.")
            % {"count": count},
            level=messages.SUCCESS,
        )

    @admin.action(description=_("Reset password/MFA lockout for selected users"))
    def reset_auth_lockouts_for_selected_users(self, request, queryset):
        require_admin_reset_permission(request)
        count = 0
        for user in queryset:
            identifiers = reset_user_auth_lockouts(user)
            log_auth_event(
                request,
                event_type="auth_lockout_reset_admin",
                success=True,
                user=user,
                username=user.get_username(),
                details={
                    "actor": request.user.get_username(),
                    "reset_identifiers": identifiers,
                    "source": "bulk_user_action",
                },
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Reset password/MFA lockout for user %(username)s") % {"username": user.get_username()},
                target_obj=user,
                details={"reset_identifiers": identifiers, "admin_action": "reset_auth_lockouts_for_selected_users"},
            )
            count += 1
        self.message_user(
            request,
            _("Password/MFA lockout counters reset for %(count)d selected user(s).") % {"count": count},
            level=messages.SUCCESS,
        )

    @admin.action(description=_("Set selected users as Disabled User"))
    def set_selected_users_disabled(self, request, queryset):
        if not can_modify_django_admin(request):
            self.message_user(request, _("You do not have permission to assign Disabled User."), level=messages.ERROR)
            return
        count = 0
        disabled_self = False
        for user in queryset:
            assign_single_role_group(user, ROLE_DISABLED_USER, clear_direct_permissions=True)
            enforce_disabled_user_exclusive(user)
            log_auth_event(
                request,
                event_type="auth_lockout_reset_admin",
                success=True,
                user=user,
                username=user.get_username(),
                details={
                    "actor": request.user.get_username(),
                    "reason": "assigned_disabled_user_role",
                    "source": "bulk_user_action",
                },
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Assigned Disabled User role to %(username)s") % {"username": user.get_username()},
                target_obj=user,
                details={"role": ROLE_DISABLED_USER, "admin_action": "set_selected_users_disabled"},
            )
            if request.user.pk == user.pk:
                disabled_self = True
            count += 1
        if count:
            self.message_user(
                request,
                _("Disabled User role assigned to %(count)d selected user(s).") % {"count": count},
                level=messages.SUCCESS,
            )
        if disabled_self:
            self.message_user(
                request,
                _("Your own account was assigned to Disabled User. On the next request, you will be redirected to the account disabled page."),
                level=messages.WARNING,
            )

    def _set_role_for_selected_users(self, request, queryset, role_name, *, admin_action, success_template):
        if not can_modify_django_admin(request):
            self.message_user(request, _("You do not have permission to modify users."), level=messages.ERROR)
            return

        count = 0
        for user in queryset:
            assign_single_role_group(
                user,
                role_name,
                clear_direct_permissions=role_name in {ROLE_DISABLED_USER, ROLE_ADMIN_USERS},
            )
            if role_name == ROLE_DISABLED_USER:
                enforce_disabled_user_exclusive(user)
            elif role_name == ROLE_ADMIN_USERS:
                enforce_admin_users_exclusive(user)
                sync_user_staff_flags_from_roles(user)
            else:
                sync_user_staff_flags_from_roles(user)

            role_status = self.account_status_display(user)
            _log_admin_explicit_action(
                request,
                action_label=_("Set role for user %(username)s to %(role)s (%(status)s)") % {
                    "username": user.get_username(),
                    "role": role_name,
                    "status": role_status,
                },
                target_obj=user,
                details={"admin_action": admin_action, "role": role_name, "account_status": str(role_status)},
            )
            count += 1

        if count:
            self.message_user(
                request,
                success_template % {"count": count},
                level=messages.SUCCESS,
            )

    @admin.action(description=_("Set selected users as Regular User"))
    def set_selected_users_regular(self, request, queryset):
        self._set_role_for_selected_users(
            request,
            queryset,
            ROLE_REGULAR_USER,
            admin_action="set_selected_users_regular",
            success_template=_("Regular User role assigned to %(count)d selected user(s)."),
        )

    @admin.action(description=_("Set selected users as Article Writer"))
    def set_selected_users_writer(self, request, queryset):
        self._set_role_for_selected_users(
            request,
            queryset,
            ROLE_ARTICLE_WRITER,
            admin_action="set_selected_users_writer",
            success_template=_("Article Writer role assigned to %(count)d selected user(s)."),
        )

    @admin.action(description=_("Set selected users as Article Approver"))
    def set_selected_users_approver(self, request, queryset):
        self._set_role_for_selected_users(
            request,
            queryset,
            ROLE_ARTICLE_APPROVER,
            admin_action="set_selected_users_approver",
            success_template=_("Article Approver role assigned to %(count)d selected user(s)."),
        )

    @admin.action(description=_("Set selected users as Article Manager"))
    def set_selected_users_manager(self, request, queryset):
        self._set_role_for_selected_users(
            request,
            queryset,
            ROLE_ARTICLE_MANAGER,
            admin_action="set_selected_users_manager",
            success_template=_("Article Manager role assigned to %(count)d selected user(s)."),
        )

    @admin.action(description=_("Set selected users as Admin Users"))
    def set_selected_users_admin(self, request, queryset):
        self._set_role_for_selected_users(
            request,
            queryset,
            ROLE_ADMIN_USERS,
            admin_action="set_selected_users_admin",
            success_template=_("Admin Users role assigned to %(count)d selected user(s)."),
        )


class UserProfileAdminForm(UserProfileAccountFormMixin, forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_account_type_help()


@admin.register(UserProfile)
class UserProfileAdmin(AdminAuditMixin, admin.ModelAdmin):
    form = UserProfileAdminForm
    list_display = (
        "user",
        "profile_account_status",
        "account_type",
        "auth_source",
        "preferred_language",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "account_type",
        "auth_source",
        "preferred_language",
        "created_at",
        "updated_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
    )
    fields = (
        "user",
        "profile_account_status",
        "account_type",
        "auth_source",
        "preferred_language",
        "notes",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("profile_account_status", "created_at", "updated_at")
    actions = ("reset_auth_lockouts_for_selected_profiles",)

    def profile_account_status(self, obj):
        if not obj or not getattr(obj, "user_id", None):
            return "-"
        return UserAdmin.account_status_display(self, obj.user)

    profile_account_status.short_description = _("Account Status")

    @admin.action(description=_("Reset password/MFA lockout for selected profiles"))
    def reset_auth_lockouts_for_selected_profiles(self, request, queryset):
        require_admin_reset_permission(request)
        count = 0
        for profile in queryset.select_related("user"):
            identifiers = reset_user_auth_lockouts(profile.user)
            log_auth_event(
                request,
                event_type="auth_lockout_reset_admin",
                success=True,
                user=profile.user,
                username=profile.user.get_username(),
                details={
                    "actor": request.user.get_username(),
                    "reset_identifiers": identifiers,
                    "source": "bulk_profile_action",
                },
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Reset password/MFA lockout for user %(username)s") % {"username": profile.user.get_username()},
                target_obj=profile.user,
                details={"reset_identifiers": identifiers, "admin_action": "reset_auth_lockouts_for_selected_profiles"},
            )
            count += 1
        self.message_user(
            request,
            _("Password/MFA lockout counters reset for %(count)d selected profile(s).") % {"count": count},
            level=messages.SUCCESS,
        )


@admin.register(UserMFADevice)
class UserMFADeviceAdmin(AdminAuditMixin, admin.ModelAdmin):
    list_display = (
        "user",
        "user_account_type",
        "confirmed",
        "confirmed_at",
        "last_verified_at",
        "reset_at",
        "created_at",
    )
    list_filter = ("confirmed", "confirmed_at", "last_verified_at", "reset_at")
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name")
    readonly_fields = ("secret_protected_display", "created_at", "confirmed_at", "last_verified_at", "reset_at", "reset_button")
    actions = ("reset_selected_mfa_devices", "mark_selected_devices_setup_pending")
    fields = ("user", "confirmed", "reset_button", "secret_protected_display", "created_at", "confirmed_at", "last_verified_at", "reset_at")


    def secret_protected_display(self, obj):
        if not obj or not obj.secret:
            return _("Not set")
        if obj.secret_is_encrypted:
            return _("Encrypted and hidden")
        return _("Not encrypted. Reset this user MFA device and ask the user to set up MFA again.")

    secret_protected_display.short_description = _("Authenticator key")

    def user_account_type(self, obj):
        profile = getattr(obj.user, "kb_profile", None)
        if not profile:
            return "-"
        return profile.get_account_type_display()

    user_account_type.short_description = _("Account Type")

    def reset_button(self, obj):
        if not obj or not obj.user_id:
            return "-"
        url = reverse("admin:kb_user_reset_mfa", args=[quote(obj.user_id)])
        return format_html(
            '<a class="button" href="{}">{}</a><p class="help">{}</p>',
            url,
            _("Reset this user's MFA"),
            _("A reset generates a fresh private authenticator key and forces setup again."),
        )

    reset_button.short_description = _("MFA Reset")

    @admin.action(description=_("Reset selected MFA devices"))
    def reset_selected_mfa_devices(self, request, queryset):
        require_admin_reset_permission(request)
        count = 0
        for device in queryset.select_related("user"):
            _device, sessions_deleted = admin_reset_user_mfa(device.user)
            log_auth_event(
                request,
                event_type="mfa_reset_admin",
                success=True,
                user=device.user,
                username=device.user.get_username(),
                details={"actor": request.user.get_username(), "sessions_deleted": sessions_deleted, "source": "bulk_device_action"},
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Reset MFA for user %(username)s") % {"username": device.user.get_username()},
                target_obj=device.user,
                details={"sessions_deleted": sessions_deleted, "admin_action": "reset_selected_mfa_devices"},
            )
            count += 1
        self.message_user(
            request,
            _("MFA reset for %(count)d selected device(s). Users must set up a new authenticator at next sign-in.")
            % {"count": count},
            level=messages.SUCCESS,
        )

    @admin.action(description=_("Mark selected MFA devices as setup pending"))
    def mark_selected_devices_setup_pending(self, request, queryset):
        require_admin_reset_permission(request)
        count = 0
        for device in queryset:
            device.confirmed = False
            device.confirmed_at = None
            device.last_verified_at = None
            device.reset_at = timezone.now()
            device.save(update_fields=["confirmed", "confirmed_at", "last_verified_at", "reset_at"])
            log_auth_event(
                request,
                event_type="mfa_reset_admin",
                success=True,
                user=device.user,
                username=device.user.get_username(),
                details={"actor": request.user.get_username(), "source": "mark_setup_pending"},
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Marked MFA setup pending for user %(username)s") % {"username": device.user.get_username()},
                target_obj=device.user,
                details={"admin_action": "mark_selected_devices_setup_pending"},
            )
            count += 1
        self.message_user(
            request,
            _("%(count)d MFA device(s) marked as setup pending.") % {"count": count},
            level=messages.SUCCESS,
        )


@admin.register(AuthActivityLog)
class AuthActivityLogAdmin(SiteSettingLogPaginationMixin, admin.ModelAdmin):
    """Read-only authentication/MFA monitoring log.

    Use this to spot repeated failed password attempts, repeated failed MFA/OTP
    attempts, MFA reset activity, and suspicious IP/user-agent patterns.
    """

    list_display = (
        "created_at",
        "event_type",
        "success",
        "username",
        "user",
        "lockout_scope_display",
        "lockout_duration_display",
        "login_mode",
        "ip_address",
        "short_user_agent",
    )
    list_filter = ("event_type", "success", "login_mode", "created_at")
    search_fields = ("username", "user__username", "user__email", "ip_address", "user_agent", "path")
    readonly_fields = (
        "created_at",
        "event_type",
        "success",
        "user",
        "username",
        "login_mode",
        "ip_address",
        "user_agent",
        "path",
        "request_method",
        "details",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 200
    list_max_show_all = 500

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Authentication activity logs are append-only from the admin UI.
        # Retention/deletion is controlled through Site settings and the cleanup command.
        return False

    def lockout_scope_display(self, obj):
        lockout_event_types = {
            AuthActivityLog.EventType.AUTH_LOCKOUT_TRIGGERED,
            AuthActivityLog.EventType.ADMIN_MFA_LOCKOUT_TRIGGERED,
        }
        if obj.event_type not in lockout_event_types:
            return "-"

        if obj.event_type == AuthActivityLog.EventType.ADMIN_MFA_LOCKOUT_TRIGGERED:
            return _("Django Admin MFA verification")

        purpose = str((obj.details or {}).get("purpose") or "").strip().lower()
        labels = {
            "password": _("Password"),
            "mfa": _("MFA verification"),
            "admin_mfa": _("Django Admin MFA verification"),
        }
        return labels.get(purpose, purpose or "-")

    lockout_scope_display.short_description = _("Lockout scope")

    def lockout_duration_display(self, obj):
        lockout_event_types = {
            AuthActivityLog.EventType.AUTH_LOCKOUT_TRIGGERED,
            AuthActivityLog.EventType.ADMIN_MFA_LOCKOUT_TRIGGERED,
        }
        if obj.event_type not in lockout_event_types:
            return "-"
        try:
            seconds = int((obj.details or {}).get("block_seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        return format_retry_after(seconds) if seconds > 0 else "-"

    lockout_duration_display.short_description = _("Lockout duration")

    def short_user_agent(self, obj):
        value = obj.user_agent or "-"
        return value[:80] + ("..." if len(value) > 80 else "")

    short_user_agent.short_description = _("User agent")


@admin.register(ActivityLog)
class ActivityLogAdmin(SiteSettingLogPaginationMixin, admin.ModelAdmin):
    """Read-only audit log for article, vote, AI, image, and admin-tool activity."""

    list_display = (
        "created_at",
        "event_type",
        "username",
        "user",
        "article_title",
        "article_status",
        "article_owner_display",
        "ip_address",
        "short_path",
    )
    list_filter = ("event_type", "article_status", "article_owner_account_type_snapshot", "created_at")
    search_fields = (
        "username",
        "user__username",
        "user__email",
        "article_title",
        "article__title",
        "article_owner_username_snapshot",
        "article_owner_name_snapshot",
        "article_owner_email_snapshot",
        "article_owner_account_type_snapshot",
        "ip_address",
        "path",
        "details",
    )
    readonly_fields = (
        "created_at",
        "event_type",
        "user",
        "username",
        "article_reference_display",
        "article_title",
        "article_status",
        "article_owner_display",
        "article_owner_user_id_snapshot",
        "article_owner_username_snapshot",
        "article_owner_name_snapshot",
        "article_owner_email_snapshot",
        "article_owner_account_type_snapshot",
        "ip_address",
        "user_agent",
        "path",
        "request_method",
        "details",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 200
    list_max_show_all = 500

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def article_reference_display(self, obj):
        if not getattr(obj, "article_id", None):
            return "-"

        try:
            return str(obj.article)
        except SuggestedArticle.DoesNotExist:
            label = obj.article_title or obj.article_id
            return _("Deleted article") + f" ({label})"

    article_reference_display.short_description = _("Article")

    def article_owner_display(self, obj):
        username = obj.article_owner_username_snapshot or ""
        name = obj.article_owner_name_snapshot or ""
        email = obj.article_owner_email_snapshot or ""
        account_type = obj.article_owner_account_type_snapshot or ""

        if not any([username, name, email, account_type, obj.article_owner_user_id_snapshot]):
            return "-"

        identity = name or username or email or str(obj.article_owner_user_id_snapshot)
        extra_parts = []
        if username and username != identity:
            extra_parts.append(username)
        if email and email not in {identity, username}:
            extra_parts.append(email)
        if account_type:
            extra_parts.append(account_type)

        if extra_parts:
            return f"{identity} ({', '.join(extra_parts)})"
        return identity

    article_owner_display.short_description = _("Article owner")

    def short_path(self, obj):
        value = obj.path or "-"
        return value[:80] + ("..." if len(value) > 80 else "")

    short_path.short_description = _("Path")


@admin.register(AdminActivityLog)
class AdminActivityLogAdmin(SiteSettingLogPaginationMixin, admin.ModelAdmin):
    """Read-only log for Django Admin create/change/delete/actions."""

    list_display = (
        "created_at",
        "action_summary",
        "admin_username",
        "admin_user",
        "target_display",
        "target_label",
        "status_display",
        "ip_address",
        "short_path",
    )
    list_filter = ("event_type", "target_app_label", "target_model", "status_code", "created_at")
    search_fields = (
        "admin_username",
        "admin_user__username",
        "admin_user__email",
        "target_app_label",
        "target_model",
        "target_object_id",
        "target_repr",
        "path",
        "ip_address",
        "change_message",
        "details",
    )
    readonly_fields = (
        "created_at",
        "action_summary",
        "event_type",
        "admin_user",
        "admin_username",
        "target_display",
        "target_label",
        "target_object_id",
        "target_app_label",
        "target_model",
        "target_repr",
        "status_display",
        "action_flag",
        "ip_address",
        "user_agent",
        "path",
        "request_method",
        "status_code",
        "change_message",
        "details",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 200
    list_max_show_all = 500

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def action_summary(self, obj):
        details = obj.details or {}
        explicit = details.get("action_label") or details.get("summary")
        if explicit:
            return explicit

        target = self.target_display(obj)
        target_label = self.target_label(obj)
        change_message = obj.change_message or ""

        if obj.event_type == AdminActivityLog.EventType.ADMIN_ADD:
            return _("Created %(target_label)s: %(target)s") % {"target_label": target_label, "target": target}
        if obj.event_type == AdminActivityLog.EventType.ADMIN_CHANGE:
            if change_message:
                return _("Changed %(target_label)s: %(target)s — %(changes)s") % {
                    "target_label": target_label,
                    "target": target,
                    "changes": change_message,
                }
            return _("Changed %(target_label)s: %(target)s") % {"target_label": target_label, "target": target}
        if obj.event_type == AdminActivityLog.EventType.ADMIN_DELETE:
            return _("Deleted %(target_label)s: %(target)s") % {"target_label": target_label, "target": target}

        admin_action = details.get("admin_action") or details.get("action")
        if admin_action:
            return _("Ran admin action '%(action)s' on %(target)s") % {"action": admin_action, "target": target}
        if obj.status_code and int(obj.status_code) >= 400:
            return _("Admin request failed or denied for %(target)s") % {"target": target}
        return change_message or (_("Admin request for %(target)s") % {"target": target})

    action_summary.short_description = _("Action")

    def target_label(self, obj):
        details = obj.details or {}
        if details.get("target_label"):
            return details["target_label"]
        if obj.target_app_label or obj.target_model:
            return f"{obj.target_app_label}.{obj.target_model}".strip(".")
        return "-"

    target_label.short_description = _("Target model")

    def target_display(self, obj):
        details = obj.details or {}
        if obj.target_repr:
            return obj.target_repr
        if details.get("target_display"):
            return details["target_display"]
        preview = details.get("selected_objects_preview") or []
        if preview:
            selected_count = int(details.get("selected_count") or len(preview))
            shown = ", ".join(str(item) for item in preview[:5])
            extra = selected_count - min(selected_count, 5)
            return f"{shown}, +{extra} more" if extra > 0 else shown
        if details.get("target_username"):
            return details["target_username"]
        if details.get("target_usernames"):
            values = details.get("target_usernames") or []
            return ", ".join(str(value) for value in values[:5])
        if obj.target_object_id:
            return _("Object ID %(object_id)s") % {"object_id": obj.target_object_id}
        return "-"

    target_display.short_description = _("Target")

    def status_display(self, obj):
        if not obj.status_code:
            return "-"
        status = int(obj.status_code)
        if 200 <= status < 300:
            label = _("OK")
        elif 300 <= status < 400:
            label = _("Redirect")
        elif status in {401, 403}:
            label = _("Denied")
        elif status == 404:
            label = _("Not found")
        elif status >= 500:
            label = _("Server error")
        else:
            label = _("Failed")
        return f"{status} - {label}"

    status_display.short_description = _("Status")

    def short_path(self, obj):
        value = obj.path or "-"
        return value[:80] + ("..." if len(value) > 80 else "")

    short_path.short_description = _("Path")


@admin.register(SuggestedArticle)
class SuggestedArticleAdmin(AdminAuditMixin, admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "author_username_snapshot",
        "author_email_snapshot",
        "visibility",
        "status",
        "update_status",
        "approved_by",
        "approved_at",
        "review_notes_preview",
        "review_notes_history_count",
        "view_count",
        "helpful_vote_count",
        "unhelpful_vote_count",
        "filename",
        "created_at",
        "updated_at",
    )
    list_filter = ("visibility", "status", "update_status", "approved_by", "approved_at", "created_at", "updated_at")
    actions = ("approve_selected_articles", "mark_selected_articles_pending_failed")
    search_fields = (
        "title",
        "body",
        "keywords",
        "owner__username",
        "owner__email",
        "author_username_snapshot",
        "author_email_snapshot",
        "review_notes",
        "review_notes_history",
        "pending_update_title",
        "pending_update_body",
        "pending_update_keywords",
        "update_status",
    )
    readonly_fields = (
        "filename",
        "raw_path",
        "wiki_path",
        "image_assets",
        "pending_update_image_assets",
        "approved_by",
        "approved_at",
        "review_notes_preview",
        "review_notes_history_count",
        "view_count",
        "helpful_vote_count",
        "unhelpful_vote_count",
        "author_username_snapshot",
        "author_name_snapshot",
        "author_email_snapshot",
        "author_account_type_snapshot",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (_("Article"), {
            "fields": ("owner", "visibility", "title", "body", "keywords", "status"),
        }),
        (_("Approval / review"), {
            "fields": ("approved_by", "approved_at", "review_notes", "review_notes_history"),
        }),
        (_("Pending update review"), {
            "fields": (
                "update_status",
                "update_submitted_at",
                "update_reviewed_at",
                "pending_update_title",
                "pending_update_body",
                "pending_update_keywords",
                "pending_update_image_assets",
            ),
        }),
        (_("OpenKB files"), {
            "fields": ("filename", "raw_path", "wiki_path", "image_assets"),
        }),
        (_("Article statistics"), {
            "fields": ("view_count", "helpful_vote_count", "unhelpful_vote_count"),
        }),
        (_("Author snapshot"), {
            "fields": (
                "author_username_snapshot",
                "author_name_snapshot",
                "author_email_snapshot",
                "author_account_type_snapshot",
            ),
        }),
        (_("Timestamps"), {
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.action(description=_("Approve selected pending articles"))
    def approve_selected_articles(self, request, queryset):
        if not can_modify_django_admin(request):
            self.message_user(request, _("You do not have permission to approve articles from Django Admin."), level=messages.ERROR)
            return
        for article in queryset:
            if article.review_notes:
                article.archive_current_review_note(actor=request.user, action="approved")
            article.review_notes = ""
            article.status = SuggestedArticle.Status.PUBLISHED
            article.approved_by = request.user
            article.approved_at = timezone.now()
            article.save(update_fields=["status", "approved_by", "approved_at", "review_notes", "review_notes_history", "updated_at"])
            write_article_files(article)
            log_activity(
                request,
                ActivityLog.EventType.ARTICLE_APPROVED,
                article=article,
                details={"source": "django_admin_bulk_action", "action": "approve_selected_articles", "visibility": article.visibility},
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Approved article %(title)s from Django Admin") % {"title": article.title},
                target_obj=article,
                details={"admin_action": "approve_selected_articles", "visibility": article.visibility},
            )

    @admin.action(description=_("Mark selected articles as pending failed"))
    def mark_selected_articles_pending_failed(self, request, queryset):
        if not can_modify_django_admin(request):
            self.message_user(request, _("You do not have permission to reject articles from Django Admin."), level=messages.ERROR)
            return
        for article in queryset:
            article.status = SuggestedArticle.Status.FAILED
            article.approved_by = None
            article.approved_at = None
            if not article.review_notes:
                article.review_notes = _("Marked as pending failed by admin. Please review this article and resubmit it for approval.")
            article.add_review_note_history(article.review_notes, reviewer=request.user, action="pending_failed")
            article.save(update_fields=["status", "approved_by", "approved_at", "review_notes", "review_notes_history", "updated_at"])
            write_article_files(article)
            log_activity(
                request,
                ActivityLog.EventType.ARTICLE_REJECTED,
                article=article,
                details={"source": "django_admin_bulk_action", "action": "mark_selected_articles_pending_failed"},
            )
            _log_admin_explicit_action(
                request,
                action_label=_("Marked article %(title)s as pending failed from Django Admin") % {"title": article.title},
                target_obj=article,
                details={"admin_action": "mark_selected_articles_pending_failed"},
            )



    def review_notes_preview(self, obj):
        if not obj.review_notes:
            return "-"
        return obj.review_notes[:80] + ("..." if len(obj.review_notes) > 80 else "")

    review_notes_preview.short_description = _("Current pending failed comments")

    def review_notes_history_count(self, obj):
        return len(obj.review_notes_history or [])

    review_notes_history_count.short_description = _("Review history")

    def helpful_vote_count(self, obj):
        return obj.votes.filter(value=ArticleVote.VoteValue.UP).count()

    helpful_vote_count.short_description = _("Likes")

    def unhelpful_vote_count(self, obj):
        return obj.votes.filter(value=ArticleVote.VoteValue.DOWN).count()

    unhelpful_vote_count.short_description = _("Dislikes")

    def save_model(self, request, obj, form, change):
        previous_status = None
        previous_review_notes = ""
        if change and obj.pk:
            previous_article = SuggestedArticle.objects.filter(pk=obj.pk).only("status", "review_notes").first()
            if previous_article:
                previous_status = previous_article.status
                previous_review_notes = previous_article.review_notes

        if not obj.filename:
            filename = ensure_article_filename(obj)
            if obj.visibility == SuggestedArticle.Visibility.INTERNAL:
                obj.raw_path = f"raw/internal/{filename}"
                obj.wiki_path = f"internal/sources/{filename}"
            else:
                obj.raw_path = f"raw/{filename}"
                obj.wiki_path = f"sources/{filename}"

        if obj.status == SuggestedArticle.Status.PUBLISHED and not obj.approved_by:
            obj.approved_by = request.user
            obj.approved_at = timezone.now()
        elif obj.status != SuggestedArticle.Status.PUBLISHED:
            obj.approved_by = None
            obj.approved_at = None

        if obj.status == SuggestedArticle.Status.FAILED:
            if not obj.review_notes:
                obj.review_notes = _("Marked as pending failed by admin. Please review this article and resubmit it for approval.")
            if obj.review_notes != previous_review_notes or previous_status != SuggestedArticle.Status.FAILED:
                obj.add_review_note_history(obj.review_notes, reviewer=request.user, action="pending_failed")
        elif obj.status in {SuggestedArticle.Status.PENDING, SuggestedArticle.Status.PUBLISHED}:
            if obj.review_notes:
                obj.archive_current_review_note(actor=request.user, action=f"cleared_on_{obj.status}")
            obj.review_notes = ""

        super().save_model(request, obj, form, change)
        write_article_files(obj)
        if change and previous_status and previous_status != obj.status:
            if obj.status == SuggestedArticle.Status.PUBLISHED:
                event_type = ActivityLog.EventType.ARTICLE_APPROVED
            elif obj.status == SuggestedArticle.Status.FAILED:
                event_type = ActivityLog.EventType.ARTICLE_REJECTED
            else:
                event_type = ActivityLog.EventType.ARTICLE_STATUS_CHANGED
        elif change:
            event_type = ActivityLog.EventType.ARTICLE_UPDATED
        else:
            event_type = ActivityLog.EventType.ARTICLE_CREATED
        log_activity(
            request,
            event_type,
            article=obj,
            details={
                "source": "django_admin_change_form",
                "change": bool(change),
                "previous_status": previous_status,
                "new_status": obj.status,
            },
        )

    def has_delete_permission(self, request, obj=None):
        # Articles should use the application deletion queue so they can be
        # restored during the configured recovery period. Permanent deletion is
        # available from My Profile → Admin tools → Article deletion queue.
        return False

    def delete_model(self, request, obj):
        log_activity(
            request,
            ActivityLog.EventType.ARTICLE_DELETED,
            article=obj,
            details={"source": "django_admin_change_form", "action": "delete_model"},
        )
        delete_article_files(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            log_activity(
                request,
                ActivityLog.EventType.ARTICLE_DELETED,
                article=obj,
                details={"source": "django_admin_bulk_delete", "action": "delete_queryset"},
            )
            delete_article_files(obj)
        super().delete_queryset(request, queryset)



@admin.register(ArticleVote)
class ArticleVoteAdmin(AdminAuditMixin, admin.ModelAdmin):
    list_display = ("article", "user", "vote_label", "created_at", "updated_at")
    list_filter = ("value", "created_at", "updated_at")
    search_fields = ("article__title", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")

    def vote_label(self, obj):
        if obj.value == ArticleVote.VoteValue.UP:
            return _("Like")
        if obj.value == ArticleVote.VoteValue.DOWN:
            return _("Dislike")
        return obj.value

    vote_label.short_description = _("Vote")


class AuthLockoutPolicyStageInline(admin.TabularInline):
    model = AuthLockoutPolicyStage
    # Do not show an unsaved default row by default. Admins can still use
    # the dynamic "Add another" button to create a new stage when needed.
    extra = 0
    fields = (
        "sort_order",
        "failure_limit",
        "block_seconds",
        "block_duration_display",
        "repeat_count",
        "enabled",
    )
    readonly_fields = ("block_duration_display",)
    ordering = ("sort_order", "id")

    def block_duration_display(self, obj):
        if not obj or obj.block_seconds in (None, ""):
            return "-"
        return format_admin_duration_with_seconds(obj.block_seconds)

    block_duration_display.short_description = _("Block duration readable")


@admin.register(SiteSetting)
class SiteSettingAdmin(AdminAuditMixin, admin.ModelAdmin):
    fieldsets = (
        (_("Article display and upload limits"), {
            "fields": ("articles_per_page", "article_image_upload_limit"),
            "description": _(
                "Controls how many articles are shown per page/on each homepage column, "
                "and how many pasted/uploaded images each article may contain. "
                "Articles per page defaults to 10. Image upload limit defaults to 50; set it to 0 to disable article image uploads."
            ),
        }),
        (_("Stray upload cleanup"), {
            "fields": ("stray_upload_cleanup_min_age_minutes",),
            "description": _(
                "Controls the minimum age used by My Profile → Admin tools → "
                "Clean stray upload files. Use 0 to show files immediately."
            ),
        }),
        (_("Article deletion queue"), {
            "fields": ("article_deletion_queue_retention_days", "article_deletion_queue_retention_display"),
            "description": _(
                "Controls how long deleted published articles stay recoverable in My Profile → Admin tools → "
                "Deletion queue before scheduled permanent deletion. Default is 7 days. Set to 0 for immediate permanent deletion after MFA confirmation."
            ),
        }),
        (_("Authentication and session settings"), {
            "fields": ("auth_activity_log_retention_days", "activity_log_retention_days", "admin_log_rows_per_page", "session_timeout_hours", "session_timeout_display"),
            "description": _(
                "Controls authentication/MFA logs, general activity logs, admin log display rows, "
                "and user session lifetime. Default log retention is 30 days. "
                "Admin log tables show 200 rows per page by default. "
                "Sessions default to 8 hours and can be set from 1 to 168 hours."
            ),
        }),
        (_("Authentication lockout policy"), {
            "fields": (
                "auth_lockout_policy_guide",
                "auth_lockout_strike_ttl_seconds",
                "auth_lockout_strike_ttl_display",
            ),
            "description": _(
                "Use the inline rows below to control progressive password/MFA lockouts. "
                "Enter durations in seconds; the admin page also shows a readable minutes/hours/days conversion. "
                "repeat_count=0 means the stage repeats forever, which should normally be used on the final row."
            ),
        }),
        (_("OpenKB AI rate limits"), {
            "fields": ("openkb_ai_prompt_limit_per_24_hours",),
            "description": _(
                "Each user's first accepted question starts a fixed 24-hour window. "
                "The existing short-term burst limit remains active separately."
            ),
        }),
        (_("Django Admin access restrictions"), {
            "fields": (
                "admin_allowed_cidrs",
                "admin_mfa_idle_timeout_seconds",
                "admin_mfa_idle_timeout_display",
            ),
            "description": _(
                "Only superusers connecting from these CIDR/IP ranges can access /admin/. "
                "Django Admin also requires an extra MFA verification. The admin MFA idle timeout controls "
                "how long the admin area may remain inactive before the admin verification is cleared. "
                "Direct /admin/login/ is always hidden with 404. Use comma or newline separated CIDR/IP values, "
                "for example: 10.65.0.0/16, 127.0.0.1/32."
            ),
        }),
    )
    readonly_fields = (
        "updated_at",
        "auth_lockout_policy_guide",
        "auth_lockout_strike_ttl_display",
        "admin_mfa_idle_timeout_display",
        "session_timeout_display",
        "article_deletion_queue_retention_display",
    )
    inlines = (AuthLockoutPolicyStageInline,)

    def get_admin_audit_extra_snapshot(self, obj):
        if not obj or not getattr(obj, "pk", None):
            return {}
        stages = []
        for stage in obj.auth_lockout_stages.all().order_by("sort_order", "id"):
            repeat = _("repeat forever") if stage.repeat_count == 0 else _("repeat %(count)s time(s)") % {"count": stage.repeat_count}
            enabled = _("enabled") if stage.enabled else _("disabled")
            stages.append(
                str(_("Stage %(order)s: %(failures)s failures -> %(duration)s, %(repeat)s, %(enabled)s"))
                % {
                    "order": stage.sort_order,
                    "failures": stage.failure_limit,
                    "duration": format_admin_duration_with_seconds(stage.block_seconds),
                    "repeat": repeat,
                    "enabled": enabled,
                }
            )
        return {
            "authentication_lockout_policy_stages": {
                "label": str(_("Authentication lockout policy stages")),
                "value": stages,
                "kind": "m2m",
            },
        }

    def auth_lockout_policy_guide(self, obj):
        return format_html(
            "<div style='max-width:900px;line-height:1.5;'>"
            "<p>{}</p>"
            "<ol>"
            "<li>{}</li>"
            "<li>{}</li>"
            "<li>{}</li>"
            "</ol>"
            "<p class='help'>{}</p>"
            "</div>",
            _(
                "The same progressive policy is used for password-login failures and MFA-code failures, "
                "but password and MFA counters are tracked separately per user."
            ),
            _("Default stage 1: 10 wrong attempts block for 5 minutes, repeated 2 times."),
            _("Default stage 2: 5 wrong attempts block for 15 minutes, repeated 2 times."),
            _("Default stage 3: 3 wrong attempts block for 1 hour repeatedly until successful login or admin reset."),
            _(
                "Successful password verification resets password lockout history. Successful MFA verification resets MFA lockout history. "
                "Admins can reset a user's counters from the User admin page."
            ),
        )

    auth_lockout_policy_guide.short_description = _("Policy guide")

    def auth_lockout_strike_ttl_display(self, obj):
        if not obj:
            return "-"
        return format_admin_duration_with_seconds(obj.auth_lockout_strike_ttl_seconds)

    auth_lockout_strike_ttl_display.short_description = _("Escalation memory readable")

    def admin_mfa_idle_timeout_display(self, obj):
        if not obj:
            return "-"
        return format_admin_duration_with_seconds(obj.admin_mfa_idle_timeout_seconds)

    admin_mfa_idle_timeout_display.short_description = _("Admin MFA idle timeout readable")

    def session_timeout_display(self, obj):
        if not obj:
            return "-"
        try:
            hours = min(max(int(obj.session_timeout_hours), 1), 168)
        except (TypeError, ValueError):
            hours = 8
        return _("%(hours)s hour(s)") % {"hours": hours}

    session_timeout_display.short_description = _("User session timeout readable")

    def article_deletion_queue_retention_display(self, obj):
        if not obj:
            return "-"
        try:
            days = max(int(obj.article_deletion_queue_retention_days), 0)
        except (TypeError, ValueError):
            days = 7
        if days == 0:
            return _("Immediate permanent deletion")
        return _("%(days)s day(s)") % {"days": days}

    article_deletion_queue_retention_display.short_description = _("Deletion queue retention readable")

    def has_add_permission(self, request):
        # Only superusers may create the singleton, and only if it does not already exist.
        return can_modify_django_admin(request) and not SiteSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent accidental removal of the settings row.
        return False


@admin.register(ArticleImageUploadLog)
class ArticleImageUploadLogAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "uploader_display",
        "uploader_email_snapshot",
        "size_kb",
        "uploaded_at",
        "deleted_at",
        "delete_reason",
    )
    list_filter = ("delete_reason", "uploaded_at", "deleted_at")
    search_fields = (
        "filename",
        "original_name",
        "uploader_username_snapshot",
        "uploader_email_snapshot",
    )
    readonly_fields = (
        "filename",
        "original_name",
        "content_type",
        "size_bytes",
        "uploaded_by",
        "uploader_username_snapshot",
        "uploader_email_snapshot",
        "uploader_account_type_snapshot",
        "upload_user_agent",
        "uploaded_at",
        "deleted_at",
        "deleted_by",
        "delete_reason",
    )
    ordering = ("-uploaded_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def uploader_display(self, obj):
        return obj.uploader_display or "-"

    uploader_display.short_description = _("Uploader display")

    def size_kb(self, obj):
        return round((obj.size_bytes or 0) / 1024, 1)

    size_kb.short_description = _("Size (KB)")
