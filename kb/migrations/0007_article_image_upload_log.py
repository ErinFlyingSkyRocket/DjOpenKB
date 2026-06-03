# Generated for DjOpenKB article image upload auditing.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0006_session_timeout_zero_browser_session"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArticleImageUploadLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("filename", models.CharField(db_index=True, max_length=255, unique=True)),
                ("original_name", models.CharField(blank=True, max_length=255)),
                ("content_type", models.CharField(blank=True, max_length=100)),
                ("size_bytes", models.PositiveIntegerField(default=0)),
                ("uploader_username_snapshot", models.CharField(blank=True, db_index=True, max_length=150)),
                ("uploader_email_snapshot", models.EmailField(blank=True, max_length=254)),
                ("uploader_account_type_snapshot", models.CharField(blank=True, max_length=50)),
                ("upload_ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("upload_user_agent", models.TextField(blank=True)),
                ("uploaded_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("delete_reason", models.CharField(blank=True, choices=[("user_removed", "Removed by uploader from editor"), ("admin_cleanup", "Deleted by admin stray-file cleanup"), ("auto_cleanup", "Deleted by automatic stray-file cleanup")], max_length=30)),
                ("deleted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="deleted_article_images", to=settings.AUTH_USER_MODEL)),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="uploaded_article_images", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Article image upload log",
                "verbose_name_plural": "Article image upload logs",
                "ordering": ["-uploaded_at"],
            },
        ),
        migrations.AddIndex(
            model_name="articleimageuploadlog",
            index=models.Index(fields=["filename"], name="kb_articlei_filenam_c7f6d4_idx"),
        ),
        migrations.AddIndex(
            model_name="articleimageuploadlog",
            index=models.Index(fields=["uploader_username_snapshot", "-uploaded_at"], name="kb_articlei_uploade_6e1e42_idx"),
        ),
        migrations.AddIndex(
            model_name="articleimageuploadlog",
            index=models.Index(fields=["-uploaded_at"], name="kb_articlei_uploade_5d2a0f_idx"),
        ),
    ]
