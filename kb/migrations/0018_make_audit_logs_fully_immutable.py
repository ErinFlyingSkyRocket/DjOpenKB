from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


AUDIT_TABLES = [
    "kb_authactivitylog",
    "kb_activitylog",
    "kb_articleimageuploadlog",
]


UPDATE_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION kb_block_audit_log_update()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit log rows are append-only and cannot be updated.';
END;
$$ LANGUAGE plpgsql;
"""


DELETE_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION kb_block_audit_log_delete_unless_retention_cleanup()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit log rows are immutable and cannot be deleted.';
END;
$$ LANGUAGE plpgsql;
"""


def enforce_fully_immutable_audit_logs(apps, schema_editor):
    """Make audit tables append-only forever at database level.

    Older migrations allowed a special retention-cleanup session variable to
    delete expired logs. This replacement intentionally ignores that variable:
    audit logs are independent historical records and must only be appended.
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(UPDATE_FUNCTION_SQL)
        cursor.execute(DELETE_FUNCTION_SQL)

        for table in AUDIT_TABLES:
            update_trigger = f"{table}_block_update"
            delete_trigger = f"{table}_block_delete"
            cursor.execute(f'DROP TRIGGER IF EXISTS {update_trigger} ON "{table}";')
            cursor.execute(f'DROP TRIGGER IF EXISTS {delete_trigger} ON "{table}";')
            cursor.execute(
                f'''
                CREATE TRIGGER {update_trigger}
                BEFORE UPDATE ON "{table}"
                FOR EACH ROW
                EXECUTE FUNCTION kb_block_audit_log_update();
                '''
            )
            cursor.execute(
                f'''
                CREATE TRIGGER {delete_trigger}
                BEFORE DELETE ON "{table}"
                FOR EACH ROW
                EXECUTE FUNCTION kb_block_audit_log_delete_unless_retention_cleanup();
                '''
            )


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0017_separate_activitylog_article_snapshot"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="authactivitylog",
            name="user",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text=(
                    "Historical user ID snapshot only. This relation intentionally does not enforce "
                    "a database constraint because audit logs must remain immutable when users are deleted."
                ),
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="auth_activity_logs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="activitylog",
            name="user",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text=(
                    "Historical user ID snapshot only. This relation intentionally does not enforce "
                    "a database constraint because audit logs must remain immutable when users are deleted."
                ),
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="activity_logs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="articleimageuploadlog",
            name="uploaded_by",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text=(
                    "Historical uploader ID snapshot only. The username/email snapshot fields keep the log readable "
                    "after the user account is changed or deleted."
                ),
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="uploaded_article_images",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="articleimageuploadlog",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text=(
                    "Historical deleter ID snapshot only. Image deletion activity is appended into ActivityLog "
                    "rather than editing this upload log row."
                ),
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="deleted_article_images",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="auth_activity_log_retention_days",
            field=models.PositiveIntegerField(
                default=0,
                help_text=(
                    "Deprecated compatibility setting. Authentication/MFA logs are immutable and are kept indefinitely. "
                    "Cleanup commands no longer delete audit logs."
                ),
                verbose_name="Authentication activity log retention (days)",
            ),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="activity_log_retention_days",
            field=models.PositiveIntegerField(
                default=0,
                help_text=(
                    "Deprecated compatibility setting. General activity logs are immutable and are kept indefinitely. "
                    "Cleanup commands no longer delete audit logs."
                ),
                verbose_name="General activity log retention (days)",
            ),
        ),
        migrations.RunPython(enforce_fully_immutable_audit_logs, migrations.RunPython.noop),
    ]
