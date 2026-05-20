# Consolidated initial migration for the current DjOpenKB kb models.
# Replace old 0001-0015 migrations with this file only for development/reset databases.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SuggestedArticle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("author_username_snapshot", models.CharField(blank=True, max_length=150)),
                ("author_name_snapshot", models.CharField(blank=True, max_length=255)),
                ("author_email_snapshot", models.EmailField(blank=True, max_length=254)),
                ("author_account_type_snapshot", models.CharField(blank=True, max_length=50)),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("keywords", models.CharField(blank=True, max_length=500)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("pending", "Pending"),
                            ("failed", "Pending failed"),
                            ("published", "Published"),
                        ],
                        default="draft",
                        max_length=20,
                    ),
                ),
                (
                    "approved_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="Date and time when this article was approved for public display.",
                        null=True,
                        verbose_name="Approved at",
                    ),
                ),
                (
                    "review_notes",
                    models.TextField(
                        blank=True,
                        help_text="Current admin feedback shown to the article owner while the article is in Draft or Pending failed status.",
                        verbose_name="Current pending failed comments",
                    ),
                ),
                (
                    "review_notes_history",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Historical review feedback entries from previous rejection/resubmission rounds.",
                        verbose_name="Pending failed comments history",
                    ),
                ),
                (
                    "view_count",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Number of unique session views for this article.",
                    ),
                ),
                ("filename", models.CharField(blank=True, max_length=255, unique=True)),
                ("raw_path", models.CharField(blank=True, max_length=500)),
                ("wiki_path", models.CharField(blank=True, max_length=500)),
                ("image_assets", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Admin user who approved this article for public display.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_articles",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Approved by",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suggested_articles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Suggested Article",
                "verbose_name_plural": "Suggested Articles",
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "account_type",
                    models.CharField(
                        choices=[
                            ("admin", "Admin"),
                            ("user", "User"),
                            ("ldap_user", "LDAP user"),
                            ("ldap_admin", "LDAP admin"),
                        ],
                        default="user",
                        help_text="Admin/LDAP admin accounts can access Django admin when staff status is enabled.",
                        max_length=20,
                    ),
                ),
                (
                    "can_access_main_site",
                    models.BooleanField(
                        default=True,
                        help_text="Untick this to block the user from accessing the main wiki site.",
                    ),
                ),
                (
                    "preferred_language",
                    models.CharField(
                        choices=settings.LANGUAGES,
                        default=settings.LANGUAGE_CODE,
                        help_text="Preferred language for the main wiki user interface.",
                        max_length=20,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kb_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Main Site User Profile",
                "verbose_name_plural": "Main Site User Profiles",
            },
        ),
        migrations.CreateModel(
            name="SiteSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "stray_upload_cleanup_min_age_minutes",
                    models.PositiveIntegerField(
                        default=30,
                        help_text=(
                            "Files newer than this many minutes are ignored by the stray upload cleanup tool. "
                            "Set to 0 to detect/delete stray uploads immediately."
                        ),
                        verbose_name="Stray upload cleanup minimum age (minutes)",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Site setting",
                "verbose_name_plural": "Site settings",
            },
        ),
        migrations.CreateModel(
            name="ArticleVote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.SmallIntegerField(choices=[(1, "Helpful"), (-1, "Not helpful")])),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "article",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="votes",
                        to="kb.suggestedarticle",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="article_votes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Article vote",
                "verbose_name_plural": "Article votes",
                "ordering": ["-updated_at"],
                "unique_together": {("article", "user")},
            },
        ),
    ]
