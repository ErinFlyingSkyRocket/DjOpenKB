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

from .models import ArticleVote, AuthActivityLog, SuggestedArticle, SiteSetting, UserMFADevice, UserProfile
from .auth_monitoring import log_auth_event
from .mfa import admin_reset_user_mfa, mfa_status_label
from .views import delete_article_files, slugify_title, write_article_files


User = get_user_model()


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fields = (
        "account_type",
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
        "main_site_access",
        "mfa_status_display",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "kb_profile__account_type",
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
        return bool(profile and profile.is_ldap_type)

    def domain_password_status(self, obj):
        return "Domain password is managed in Active Directory and cannot be changed from Django admin."

    domain_password_status.short_description = "Password"

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
            raise Http404("Domain user passwords are managed in Active Directory.")
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

    main_site_account_type.short_description = "Account Type"

    def main_site_access(self, obj):
        profile = getattr(obj, "kb_profile", None)

        if not obj.is_active:
            return "Inactive"

        if profile and profile.can_access_main_site:
            return "Allowed"

        return "Blocked"

    main_site_access.short_description = "Main Site Access"


    def mfa_status_display(self, obj):
        status = mfa_status_label(obj)
        if status == "Configured":
            return format_html('<span style="color:#0a7a2f;font-weight:600;">{}</span>', status)
        if status == "Setup pending":
            return format_html('<span style="color:#a15c00;font-weight:600;">{}</span>', status)
        return format_html('<span style="color:#8a1f11;font-weight:600;">{}</span>', status)

    mfa_status_display.short_description = "MFA Status"

    def mfa_reset_button(self, obj):
        if not obj or not obj.pk:
            return "-"
        url = reverse("admin:kb_user_reset_mfa", args=[quote(obj.pk)])
        return format_html(
            '<a class="button" href="{}">Reset MFA</a><p class="help">'
            'Resets this user\'s authenticator and requires a fresh QR setup. '
            'The new authenticator key is private and is not displayed to admins.</p>',
            url,
        )

    mfa_reset_button.short_description = "MFA Reset"

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
            raise Http404("User does not exist.")

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

    @admin.action(description="Reset MFA for selected users")
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

    @admin.action(description="Allow selected users to access main site")
    def allow_main_site_access(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.can_access_main_site = True
            profile.save(update_fields=["can_access_main_site", "updated_at"])

    @admin.action(description="Block selected users from main site")
    def block_main_site_access(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.can_access_main_site = False
            profile.save(update_fields=["can_access_main_site", "updated_at"])

    @admin.action(description="Set selected users as User")
    def make_django_user(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.USER
            profile.save(update_fields=["account_type", "updated_at"])

    @admin.action(description="Set selected users as Admin")
    def make_django_admin(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.ADMIN
            profile.save(update_fields=["account_type", "updated_at"])

    @admin.action(description="Set selected users as LDAP user")
    def make_ldap_user(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.LDAP_USER
            profile.save(update_fields=["account_type", "updated_at"])

    @admin.action(description="Set selected users as LDAP admin")
    def make_ldap_admin(self, request, queryset):
        for user in queryset:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.account_type = UserProfile.AccountType.LDAP_ADMIN
            profile.save(update_fields=["account_type", "updated_at"])


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "account_type",
        "can_access_main_site",
        "preferred_language",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "account_type",
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
    readonly_fields = ("secret", "created_at", "confirmed_at", "last_verified_at", "reset_at", "reset_button")
    actions = ("reset_selected_mfa_devices", "mark_selected_devices_setup_pending")
    fields = ("user", "confirmed", "reset_button", "secret", "created_at", "confirmed_at", "last_verified_at", "reset_at")

    def user_account_type(self, obj):
        profile = getattr(obj.user, "kb_profile", None)
        if not profile:
            return "-"
        return profile.get_account_type_display()

    user_account_type.short_description = "Account Type"

    def reset_button(self, obj):
        if not obj or not obj.user_id:
            return "-"
        url = reverse("admin:kb_user_reset_mfa", args=[quote(obj.user_id)])
        return format_html(
            "<a class=\"button\" href=\"{}\">Reset this user&#x27;s MFA</a><p class=\"help\">"
            'A reset generates a fresh private authenticator key and forces setup again.</p>',
            url,
        )

    reset_button.short_description = "MFA Reset"

    @admin.action(description="Reset selected MFA devices")
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

    @admin.action(description="Mark selected MFA devices as setup pending")
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
class AuthActivityLogAdmin(admin.ModelAdmin):
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

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Authentication activity logs are append-only from the admin UI.
        # Retention/deletion is controlled through Site settings and the cleanup command.
        return False

    def short_user_agent(self, obj):
        value = obj.user_agent or "-"
        return value[:80] + ("..." if len(value) > 80 else "")

    short_user_agent.short_description = "User agent"


@admin.register(SuggestedArticle)
class SuggestedArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "author_username_snapshot",
        "author_email_snapshot",
        "status",
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
    list_filter = ("status", "approved_by", "approved_at", "created_at", "updated_at")
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
    )
    readonly_fields = (
        "filename",
        "raw_path",
        "wiki_path",
        "image_assets",
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
        ("Article", {
            "fields": ("owner", "title", "body", "keywords", "status"),
        }),
        (_("Approval / review"), {
            "fields": ("approved_by", "approved_at", "review_notes", "review_notes_history"),
        }),
        ("OpenKB files", {
            "fields": ("filename", "raw_path", "wiki_path", "image_assets"),
        }),
        ("Article statistics", {
            "fields": ("view_count", "helpful_vote_count", "unhelpful_vote_count"),
        }),
        ("Author snapshot", {
            "fields": (
                "author_username_snapshot",
                "author_name_snapshot",
                "author_email_snapshot",
                "author_account_type_snapshot",
            ),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.action(description="Approve selected pending articles")
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

    @admin.action(description="Mark selected articles as pending failed")
    def mark_selected_articles_pending_failed(self, request, queryset):
        for article in queryset:
            article.status = SuggestedArticle.Status.FAILED
            article.approved_by = None
            article.approved_at = None
            if not article.review_notes:
                article.review_notes = "Marked as pending failed by admin. Please review this article and resubmit it for approval."
            article.add_review_note_history(article.review_notes, reviewer=request.user, action="pending_failed")
            article.save(update_fields=["status", "approved_by", "approved_at", "review_notes", "review_notes_history", "updated_at"])
            write_article_files(article)



    def review_notes_preview(self, obj):
        if not obj.review_notes:
            return "-"
        return obj.review_notes[:80] + ("..." if len(obj.review_notes) > 80 else "")

    review_notes_preview.short_description = "Current pending failed comments"

    def review_notes_history_count(self, obj):
        return len(obj.review_notes_history or [])

    review_notes_history_count.short_description = "Review history"

    def helpful_vote_count(self, obj):
        return obj.votes.filter(value=ArticleVote.VoteValue.UP).count()

    helpful_vote_count.short_description = "Likes"

    def unhelpful_vote_count(self, obj):
        return obj.votes.filter(value=ArticleVote.VoteValue.DOWN).count()

    unhelpful_vote_count.short_description = "Dislikes"

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
                obj.review_notes = "Marked as pending failed by admin. Please review this article and resubmit it for approval."
            if obj.review_notes != previous_review_notes or previous_status != SuggestedArticle.Status.FAILED:
                obj.add_review_note_history(obj.review_notes, reviewer=request.user, action="pending_failed")
        elif obj.status in {SuggestedArticle.Status.PENDING, SuggestedArticle.Status.PUBLISHED}:
            if obj.review_notes:
                obj.archive_current_review_note(actor=request.user, action=f"cleared_on_{obj.status}")
            obj.review_notes = ""

        super().save_model(request, obj, form, change)
        write_article_files(obj)

    def delete_model(self, request, obj):
        delete_article_files(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
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
            return "Like"
        if obj.value == ArticleVote.VoteValue.DOWN:
            return "Dislike"
        return obj.value

    vote_label.short_description = "Vote"


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Stray upload cleanup", {
            "fields": ("stray_upload_cleanup_min_age_minutes",),
            "description": (
                "Controls the minimum age used by My Profile → Admin tools → "
                "Clean stray upload files. Use 0 to show files immediately."
            ),
        }),
        ("Authentication and session settings", {
            "fields": ("auth_activity_log_retention_days", "session_timeout_days"),
            "description": (
                "Controls authentication/MFA log retention and user session lifetime. "
                "The default session timeout is 30 days. Set session timeout to 0 to expire the session when the browser closes."
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
