from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0008_userprofile_auth_source"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActivityLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("article_created", "Article created"),
                            ("article_updated", "Article updated"),
                            ("article_deleted", "Article deleted"),
                            ("article_status_changed", "Article status changed"),
                            ("article_submitted", "Article submitted for approval"),
                            ("article_approved", "Article approved/published"),
                            ("article_rejected", "Article marked pending failed"),
                            ("article_orphan_assigned", "Orphan article assigned"),
                            ("article_orphan_deleted", "Orphan article deleted"),
                            ("article_viewed", "Article viewed"),
                            ("vote_up", "Article vote up"),
                            ("vote_down", "Article vote down"),
                            ("vote_updated", "Article vote changed"),
                            ("vote_removed", "Article vote removed"),
                            ("image_uploaded", "Article image uploaded"),
                            ("image_deleted", "Article image deleted"),
                            ("ai_question", "OpenKB AI question"),
                            ("ai_rate_limited", "OpenKB AI rate limited"),
                            ("bulk_import", "Bulk article import"),
                            ("admin_tool_action", "Admin tool action"),
                        ],
                        db_index=True,
                        max_length=60,
                    ),
                ),
                ("username", models.CharField(blank=True, db_index=True, max_length=255)),
                ("article_title", models.CharField(blank=True, db_index=True, max_length=255)),
                ("article_status", models.CharField(blank=True, db_index=True, max_length=40)),
                ("ip_address", models.GenericIPAddressField(blank=True, db_index=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                ("path", models.CharField(blank=True, max_length=500)),
                ("request_method", models.CharField(blank=True, max_length=10)),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "article",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="activity_logs",
                        to="kb.suggestedarticle",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="activity_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Activity log",
                "verbose_name_plural": "Activity logs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="activity_log_retention_days",
            field=models.PositiveIntegerField(
                default=90,
                help_text=(
                    "Article/vote/image/admin-tool activity logs older than this many days can be deleted by the cleanup command. "
                    "Use 0 to keep general activity logs indefinitely."
                ),
                verbose_name="General activity log retention (days)",
            ),
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(fields=["-created_at", "event_type"], name="kb_activity_created_34f83d_idx"),
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(fields=["username", "-created_at"], name="kb_activity_usernam_e4c3d4_idx"),
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(fields=["article_title", "-created_at"], name="kb_activity_article_0387e8_idx"),
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(fields=["ip_address", "-created_at"], name="kb_activity_ip_addr_709e8b_idx"),
        ),
    ]
