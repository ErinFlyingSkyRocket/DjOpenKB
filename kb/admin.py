from django.contrib import admin

from django.utils import timezone

from .models import SuggestedArticle
from .views import delete_article_files, slugify_title, write_article_files


@admin.register(SuggestedArticle)
class SuggestedArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "status",
        "filename",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "created_at", "updated_at")
    search_fields = ("title", "body", "keywords", "owner__username", "owner__email")
    readonly_fields = ("filename", "raw_path", "wiki_path", "image_assets", "created_at", "updated_at")
    fieldsets = (
        ("Article", {
            "fields": ("owner", "title", "body", "keywords", "status"),
        }),
        ("OpenKB files", {
            "fields": ("filename", "raw_path", "wiki_path", "image_assets"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def save_model(self, request, obj, form, change):
        if not obj.filename:
            timestamp_slug = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
            obj.filename = f"{timestamp_slug}-{slugify_title(obj.title)}.md"
            obj.raw_path = f"raw/{obj.filename}"
            obj.wiki_path = f"sources/{obj.filename}"

        super().save_model(request, obj, form, change)
        write_article_files(obj)

    def delete_model(self, request, obj):
        delete_article_files(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            delete_article_files(obj)
        super().delete_queryset(request, queryset)
