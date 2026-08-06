import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0011_security_hardening_title_and_upload_quotas"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="stray_upload_cleanup_min_age_minutes",
            field=models.PositiveIntegerField(
                default=1440,
                help_text=(
                    "Ordinary stray files newer than this many minutes are ignored by cleanup. "
                    "The same value controls abandoned new-article workspaces, but a workspace is never "
                    "automatically discarded earlier than 1440 minutes (24 hours). Set to 0 only for "
                    "immediate ordinary stray-file detection/deletion."
                ),
                verbose_name="Stray upload cleanup minimum age (minutes)",
            ),
        ),
        migrations.CreateModel(
            name="ArticleCreationWorkspace",
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
                ("image_assets", models.JSONField(blank=True, default=list)),
                ("is_dirty", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "owner",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="article_creation_workspace",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Article creation workspace",
                "verbose_name_plural": "Article creation workspaces",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddField(
            model_name="articleimageuploadlog",
            name="creation_workspace_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                editable=False,
                help_text=(
                    "Temporary new-article workspace that owned this upload when it was created. "
                    "The UUID is retained as an audit snapshot after the workspace is removed."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="articleimageuploadlog",
            name="editing_article_id",
            field=models.PositiveBigIntegerField(
                blank=True,
                db_index=True,
                editable=False,
                help_text=(
                    "Existing article being edited when this upload was created. "
                    "This is an audit snapshot and does not enforce a database relation."
                ),
                null=True,
            ),
        ),
    ]
