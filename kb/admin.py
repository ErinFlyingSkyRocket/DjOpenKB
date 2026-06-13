from django.contrib import admin, messages
from django.contrib.admin.utils import quote
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import ActivityLog, ArticleImageUploadLog, ArticleVote, AuthActivityLog, SuggestedArticle, SiteSetting, UserMFADevice, UserProfile
from .auth_monitoring import log_auth_event
from .mfa import admin_reset_user_mfa, mfa_status_label
from .views import delete_article_files, log_activity, slugify_title, write_article_files


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
    _set_admin_model_label(SuggestedArticle, "Suggested Article", "Suggested Articles")
    _set_admin_model_label(ArticleVote, "Article vote", "Article votes")
    _set_admin_model_label(SiteSetting, "Site setting", "Site settings")
    _set_admin_model_label(ArticleImageUploadLog, "Article image upload log", "Article image upload logs")
    _set_admin_model_label(ActivityLog, "Activity log", "Activity logs")

    labels = {
        UserProfile: {
            "user": "User",
            "account_type": "Account Type",
            "auth_source": "Source",
            "can_access_main_site": "Main Site Access",
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
        ActivityLog: {
            "created_at": "Created at",
            "event_type": "Event type",
            "user": "User",
            "username": "Username",
            "article_id": "Article ID",
            "article_title": "Article title",
            "article_status": "Article status",
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
            "article_image_upload_limit": "Article image upload limit",
            "auth_activity_log_retention_days": "Authentication activity log retention (days)",
            "session_timeout_days": "User session timeout (days)",
            "activity_log_retention_days": "General activity log retention (days)",
            "admin_log_rows_per_page": "Admin log rows per page",
            "updated_at": "Updated at",
        },
    }

    help_texts = {
        (UserProfile, "account_type"): "Admin/LDAP admin accounts can access Django admin when staff status is enabled.",
        (UserProfile, "auth_source"): "Controls whether the password is managed locally in DjOpenKB or externally by Active Directory.",
        (UserProfile, "can_access_main_site"): "Untick this to block the user from accessing the main wiki site.",
        (UserProfile, "preferred_language"): "Preferred language for the main wiki user interface.",
        (SiteSetting, "stray_upload_cleanup_min_age_minutes"): "Files newer than this many minutes are ignored by the stray upload cleanup tool. Default is 1440 minutes (24 hours) to avoid deleting images while users are drafting articles. Set to 0 to detect/delete stray uploads immediately.",
        (SiteSetting, "article_image_upload_limit"): "Maximum number of pasted/uploaded images allowed per article, including draft, pending, published, and pending-update versions. Default is 50. Set to 0 to disable article image uploads.",
        (SiteSetting, "auth_activity_log_retention_days"): "Authentication/MFA monitoring logs older than this many days can be deleted by the cleanup command. Use 0 to keep authentication activity logs indefinitely.",
        (SiteSetting, "session_timeout_days"): "Authenticated user sessions expire after this many days from sign-in. After expiry, users are signed out and must log in again. Set to 0 to expire the session when the browser closes.",
        (SiteSetting, "activity_log_retention_days"): "Article/vote/image/admin-tool activity logs older than this many days can be deleted by the cleanup command. Use 0 to keep general activity logs indefinitely.",
        (SiteSetting, "admin_log_rows_per_page"): "Number of rows to show per page in Django Admin log tables. Recommended range: 50 to 500. Default is 200.",
    }

    for model, field_labels in labels.items():
        for field_name, label in field_labels.items():
            _set_admin_field_label(model, field_name, label, help_texts.get((model, field_name)))


_apply_admin_translation_labels()



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

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fields = (
        "account_type",
        "auth_source",
        "can_access_main_site",
        "preferred_language",
        "notes",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(DefaultUserAdmin):
    inlines = (UserProfileInline,)

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_active",
        "is_staff",
        "is_superuser",
        "main_site_account_type",
        "main_site_auth_source",
        "main_site_access",
        "mfa_status_display",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "kb_profile__account_type",
        "kb_profile__auth_source",
        "kb_profile__can_access_main_site",
        "kb_mfa_device__confirmed",
    )
    search_fields = (
        "username",
        "email",
        "first_name",
        "last_name",
    )
    actions = (
        "allow_main_site_access",
        "block_main_site_access",
        "make_django_user",
        "make_django_admin",
        "make_ldap_user",
        "make_ldap_admin",
        "reset_mfa_for_selected_users",
    )

    def _is_domain_user(self, obj):
        """Return True when this Django user is managed by AD/LDAP."""
        profile = getattr(obj, "kb_profile", None)
        return bool(profile and getattr(profile, "is_ad_managed", False))

    def domain_password_status(self, obj):
        return _("Domain password is managed in Active Directory and cannot be changed from Django admin.")

    domain_password_status.short_description = _("Password")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and self._is_domain_user(obj) and "domain_password_status" not in readonly_fields:
            readonly_fields.append("domain_password_status")
        if obj:
            for field in ("mfa_status_display", "mfa_reset_button"):
                if field not in readonly_fields:
                    readonly_fields.append(field)
        return tuple(readonly_fields)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)

        cleaned_fieldsets = []
        for title, options in fieldsets:
            options = dict(options)
            fields = options.get("fields", ())

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

        return tuple(cleaned_fieldsets)

    def user_change_password(self, request, id, form_url=""):
        obj = self.get_object(request, id)
        if obj and self._is_domain_user(obj):
            raise Http404(_("Domain user passwords are managed in Active Directory."))
        return super().user_change_password(request, id, form_url)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        profile, created = UserProfile.objects.get_or_create(user=obj)
        if obj.is_superuser or obj.is_staff:
            if profile.account_type not in {
                UserProfile.AccountType.ADMIN,
                UserProfile.AccountType.LDAP_ADMIN,
            }:
                profile.account_type = UserProfile.AccountType.ADMIN
                profile.save(update_fields=["account_type", "updated_at"])

    def main_site_account_type(self, obj):
        profile = getattr(obj, "kb_profile", None)
        if not profile:
            return "-"
        return profile.get_account_type_display()

    main_site_account_type.short_description = _("Account Type")

    def main_site_auth_source(self, obj):
        profile = getattr(obj, "kb_profile", None)
        if not profile:
            return "-"
        return profile.get_auth_source_display()

    main_site_auth_source.short_description = _("Source")

    def main_site_access(self, obj):
        profile = getattr(obj, "kb_profile", None)

        if not obj.is_active:
            return _("Inactive")

        if profile and profile.can_access_main_site:
            return _("Allowed")

        return _("Blocked")

    main_site_access.short_description = _("Main Site Access")


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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:user_id>/reset-mfa/",
                self.admin_site.admin_view(self.reset_user_mfa_view),
                name="kb_user_reset_mfa",
            ),
        ]
        return custom_urls + urls

    def reset_user_mfa_view(self, request, user_id):
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
            count += 1
        self.message_user(
            request,
            _("MFA reset for %(count)d selected user(s). They must set up a new authenticator at next sign-in.")
            % {"count": count},
            level=messages.SUCCESS,
        )

    @admin.action(description=_("Allow selected users to access main site"))
    def allow_main_site_access(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.can_access_main_site = True
            profile.save(update_fields=["can_access_main_site", "updated_at"])

    @admin.action(description=_("Block selected users from main site"))
    def block_main_site_access(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.can_access_main_site = False
            profile.save(update_fields=["can_access_main_site", "updated_at"])

    @admin.action(description=_("Set selected users as User"))
    def make_django_user(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.USER
            profile.auth_source = UserProfile.AuthSource.LOCAL
            profile.save(update_fields=["account_type", "auth_source", "updated_at"])

    @admin.action(description=_("Set selected users as Admin"))
    def make_django_admin(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.ADMIN
            profile.auth_source = UserProfile.AuthSource.LOCAL
            profile.save(update_fields=["account_type", "auth_source", "updated_at"])

    @admin.action(description=_("Set selected users as LDAP user"))
    def make_ldap_user(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.LDAP_USER
            profile.auth_source = UserProfile.AuthSource.AD
            profile.save(update_fields=["account_type", "auth_source", "updated_at"])

    @admin.action(description=_("Set selected users as LDAP admin"))
    def make_ldap_admin(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.LDAP_ADMIN
            profile.auth_source = UserProfile.AuthSource.AD
            profile.save(update_fields=["account_type", "auth_source", "updated_at"])


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "account_type",
        "auth_source",
        "can_access_main_site",
        "preferred_language",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "account_type",
        "auth_source",
        "can_access_main_site",
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
        "account_type",
        "auth_source",
        "can_access_main_site",
        "preferred_language",
        "notes",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserMFADevice)
class UserMFADeviceAdmin(admin.ModelAdmin):
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
            count += 1
        self.message_user(
            request,
            _("MFA reset for %(count)d selected device(s). Users must set up a new authenticator at next sign-in.")
            % {"count": count},
            level=messages.SUCCESS,
        )

    @admin.action(description=_("Mark selected MFA devices as setup pending"))
    def mark_selected_devices_setup_pending(self, request, queryset):
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
        "user_reference_display",
        "login_mode",
        "ip_address",
        "short_user_agent",
    )
    list_filter = ("event_type", "success", "login_mode", "created_at")
    search_fields = ("username", "ip_address", "user_agent", "path", "details")
    readonly_fields = (
        "created_at",
        "event_type",
        "success",
        "user_reference_display",
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
        # Authentication activity logs are immutable from both the admin UI and database layer.
        return False

    def user_reference_display(self, obj):
        if obj.username:
            return obj.username
        if getattr(obj, "user_id", None):
            return _("Deleted user") + f" #{obj.user_id}"
        return "-"

    user_reference_display.short_description = _("User")

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
        "user_reference_display",
        "article_title",
        "article_status",
        "ip_address",
        "short_path",
    )
    list_filter = ("event_type", "article_status", "created_at")
    search_fields = (
        "username",
        "article_title",
        "article_id",
        "ip_address",
        "path",
        "details",
    )
    readonly_fields = (
        "created_at",
        "event_type",
        "user_reference_display",
        "username",
        "article_reference_display",
        "article_id",
        "article_title",
        "article_status",
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

    def user_reference_display(self, obj):
        if obj.username:
            return obj.username
        if getattr(obj, "user_id", None):
            return _("Deleted user") + f" #{obj.user_id}"
        return "-"

    user_reference_display.short_description = _("User")

    def article_reference_display(self, obj):
        if not getattr(obj, "article_id", None):
            return "-"

        label = obj.article_title or obj.article_id
        return _("Article snapshot") + f" #{obj.article_id} ({label})"

    article_reference_display.short_description = _("Article")

    def short_path(self, obj):
        value = obj.path or "-"
        return value[:80] + ("..." if len(value) > 80 else "")

    short_path.short_description = _("Path")


@admin.register(SuggestedArticle)
class SuggestedArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "author_username_snapshot",
        "author_email_snapshot",
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
    list_filter = ("status", "update_status", "approved_by", "approved_at", "created_at", "updated_at")
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
            "fields": ("owner", "title", "body", "keywords", "status"),
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
                details={"source": "django_admin_bulk_action", "action": "approve_selected_articles"},
            )

    @admin.action(description=_("Mark selected articles as pending failed"))
    def mark_selected_articles_pending_failed(self, request, queryset):
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
            timestamp_slug = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
            obj.filename = f"{timestamp_slug}-{slugify_title(obj.title)}.md"
            obj.raw_path = f"raw/{obj.filename}"
            obj.wiki_path = f"sources/{obj.filename}"

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
class ArticleVoteAdmin(admin.ModelAdmin):
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


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    fieldsets = (
        (_("Article upload limits"), {
            "fields": ("article_image_upload_limit",),
            "description": _(
                "Controls how many pasted/uploaded images each article may contain. "
                "Default is 50. Set to 0 to disable article image uploads."
            ),
        }),
        (_("Stray upload cleanup"), {
            "fields": ("stray_upload_cleanup_min_age_minutes",),
            "description": _(
                "Controls the minimum age used by My Profile → Admin tools → "
                "Clean stray upload files. Use 0 to show files immediately."
            ),
        }),
        (_("Authentication and session settings"), {
            "fields": ("auth_activity_log_retention_days", "activity_log_retention_days", "admin_log_rows_per_page", "session_timeout_days"),
            "description": _(
                "Controls authentication/MFA logs, general activity logs, admin log display rows, "
                "and user session lifetime. Default log retention is 30 days. "
                "Admin log tables show 200 rows per page by default. "
                "Set session timeout to 0 to expire the session when the browser closes."
            ),
        }),
    )
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        # Only allow creating the singleton if it does not already exist.
        return not SiteSetting.objects.exists()

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
        "uploader_reference_display",
        "uploader_username_snapshot",
        "uploader_email_snapshot",
        "uploader_account_type_snapshot",
        "upload_user_agent",
        "uploaded_at",
        "deleted_at",
        "deleter_reference_display",
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

    def uploader_reference_display(self, obj):
        value = obj.uploader_display or ""
        if value:
            return value
        if getattr(obj, "uploaded_by_id", None):
            return _("Deleted user") + f" #{obj.uploaded_by_id}"
        return "-"

    uploader_reference_display.short_description = _("Uploaded by")

    def deleter_reference_display(self, obj):
        if getattr(obj, "deleted_by_id", None):
            return _("Deleted user") + f" #{obj.deleted_by_id}"
        return "-"

    deleter_reference_display.short_description = _("Deleted by")

    def size_kb(self, obj):
        return round((obj.size_bytes or 0) / 1024, 1)

    size_kb.short_description = _("Size (KB)")
