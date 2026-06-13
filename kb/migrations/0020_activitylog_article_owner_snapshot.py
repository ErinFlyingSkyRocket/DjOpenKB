from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0019_restore_audit_log_retention_cleanup"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="activitylog",
                    name="article_id",
                ),
                migrations.AddField(
                    model_name="activitylog",
                    name="article",
                    field=models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        help_text=(
                            "Snapshot fields keep the audit trail after an article is deleted. "
                            "This relation intentionally does not enforce a database constraint because audit logs are append-only."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="activity_logs",
                        to="kb.suggestedarticle",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="activitylog",
            name="article_owner_user_id_snapshot",
            field=models.PositiveIntegerField(
                blank=True,
                db_index=True,
                help_text="Historical user ID of the account that owned the article when this log was created.",
                null=True,
                verbose_name="Article owner user ID snapshot",
            ),
        ),
        migrations.AddField(
            model_name="activitylog",
            name="article_owner_username_snapshot",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=255,
                verbose_name="Article owner username snapshot",
            ),
        ),
        migrations.AddField(
            model_name="activitylog",
            name="article_owner_name_snapshot",
            field=models.CharField(
                blank=True,
                max_length=255,
                verbose_name="Article owner name snapshot",
            ),
        ),
        migrations.AddField(
            model_name="activitylog",
            name="article_owner_email_snapshot",
            field=models.EmailField(
                blank=True,
                max_length=254,
                verbose_name="Article owner email snapshot",
            ),
        ),
        migrations.AddField(
            model_name="activitylog",
            name="article_owner_account_type_snapshot",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=50,
                verbose_name="Article owner account type snapshot",
            ),
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(
                fields=["article_owner_username_snapshot", "-created_at"],
                name="kb_act_owner_cr_idx",
            ),
        ),
    ]
