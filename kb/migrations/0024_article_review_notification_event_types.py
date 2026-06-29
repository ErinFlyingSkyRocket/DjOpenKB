# Generated for SMTP article-review notification audit event labels.

from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


ACTIVITY_EVENT_CHOICES = [
    ("article_created", _("Article created")),
    ("article_updated", _("Article updated")),
    ("article_deleted", _("Article deleted")),
    ("article_delete_queued", _("Article queued for deletion")),
    ("article_delete_restored", _("Article restored from deletion queue")),
    ("article_delete_purged", _("Article permanently deleted")),
    ("article_delete_auto_purged", _("Article auto-deleted from queue")),
    ("article_deletion_requested", _("Article deletion requested")),
    ("article_deletion_rejected", _("Article deletion rejected")),
    ("article_status_changed", _("Article status changed")),
    ("article_submitted", _("Article submitted for approval")),
    ("article_approved", _("Article approved/published")),
    ("article_rejected", _("Article marked pending failed")),
    ("article_review_notification_queued", _("Article review notification queued")),
    ("article_review_notification_sent", _("Article review notification sent")),
    ("article_review_notification_failed", _("Article review notification failed")),
    ("article_review_notification_skipped", _("Article review notification skipped")),
    ("article_orphan_assigned", _("Orphan article assigned")),
    ("article_orphan_deleted", _("Orphan article deleted")),
    ("article_viewed", _("Article viewed")),
    ("vote_up", _("Article vote up")),
    ("vote_down", _("Article vote down")),
    ("vote_updated", _("Article vote changed")),
    ("vote_removed", _("Article vote removed")),
    ("image_uploaded", _("Article image uploaded")),
    ("image_deleted", _("Article image deleted")),
    ("ai_question", _("OpenKB AI question")),
    ("ai_rate_limited", _("OpenKB AI rate limited")),
    ("bulk_import", _("Bulk article import")),
    ("profile_email_updated", _("Profile email updated")),
    ("profile_password_changed", _("Profile password changed")),
    ("admin_tool_action", _("Admin tool action")),
]


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0023_session_timeout_hours"),
    ]

    operations = [
        migrations.AlterField(
            model_name="activitylog",
            name="event_type",
            field=models.CharField(
                choices=ACTIVITY_EVENT_CHOICES,
                db_index=True,
                max_length=60,
            ),
        ),
    ]
