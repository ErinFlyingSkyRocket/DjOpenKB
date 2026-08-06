import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0014_account_deletion_checkpoint_cleanup"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArticleEditWorkspace",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "editor_mode",
                    models.CharField(
                        choices=[("edit", "Edit"), ("review", "Review")],
                        default="edit",
                        max_length=10,
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=200)),
                ("body", models.TextField(blank=True)),
                ("keywords", models.CharField(blank=True, max_length=500)),
                (
                    "visibility",
                    models.CharField(
                        choices=[
                            ("public", "Public article"),
                            ("internal", "Internal article"),
                        ],
                        default="public",
                        max_length=20,
                    ),
                ),
                ("status", models.CharField(blank=True, max_length=20)),
                ("review_notes", models.TextField(blank=True)),
                ("image_assets", models.JSONField(blank=True, default=list)),
                (
                    "owned_image_assets",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            "Uncommitted images uploaded specifically into this edit checkpoint."
                        ),
                    ),
                ),
                ("is_dirty", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "article",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="edit_workspaces",
                        to="kb.suggestedarticle",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="article_edit_workspaces",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Article edit workspace",
                "verbose_name_plural": "Article edit workspaces",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="articleeditworkspace",
            constraint=models.UniqueConstraint(
                fields=("owner", "article", "editor_mode"),
                name="kb_unique_article_edit_workspace",
            ),
        ),
        migrations.AddField(
            model_name="articleimageuploadlog",
            name="edit_workspace_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                editable=False,
                help_text=(
                    "Existing-article checkpoint that owned this upload when it was created. "
                    "The UUID is retained as an audit snapshot after the workspace is removed."
                ),
                null=True,
            ),
        ),
    ]
