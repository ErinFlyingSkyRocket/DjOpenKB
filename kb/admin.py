from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import ArticleVote, SuggestedArticle, SiteSetting, UserProfile
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
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "kb_profile__account_type",
        "kb_profile__can_access_main_site",
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
    )

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
    )
    readonly_fields = (
        "filename",
        "raw_path",
        "wiki_path",
        "image_assets",
        "approved_by",
        "approved_at",
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
        (_("Approval"), {
            "fields": ("approved_by", "approved_at"),
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
            article.status = SuggestedArticle.Status.PUBLISHED
            article.approved_by = request.user
            article.approved_at = timezone.now()
            article.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
            write_article_files(article)

    @admin.action(description="Mark selected articles as pending failed")
    def mark_selected_articles_pending_failed(self, request, queryset):
        for article in queryset:
            article.status = SuggestedArticle.Status.FAILED
            article.approved_by = None
            article.approved_at = None
            article.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
            write_article_files(article)



    def helpful_vote_count(self, obj):
        return obj.votes.filter(value=ArticleVote.VoteValue.UP).count()

    helpful_vote_count.short_description = "Likes"

    def unhelpful_vote_count(self, obj):
        return obj.votes.filter(value=ArticleVote.VoteValue.DOWN).count()

    unhelpful_vote_count.short_description = "Dislikes"

    def save_model(self, request, obj, form, change):
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
    )
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        # Only allow creating the singleton if it does not already exist.
        return not SiteSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent accidental removal of the settings row.
        return False
