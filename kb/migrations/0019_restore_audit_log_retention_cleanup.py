from django.db import migrations, models


AUDIT_TABLES = [
    "kb_authactivitylog",
    "kb_activitylog",
    "kb_articleimageuploadlog",
]


DELETE_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION kb_block_audit_log_delete_unless_retention_cleanup()
RETURNS trigger AS $$
BEGIN
    IF COALESCE(current_setting('djopenkb.audit_retention_cleanup', true), '') = 'on' THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION 'Audit log rows cannot be manually deleted. They are removed only by retention cleanup.';
END;
$$ LANGUAGE plpgsql;
"""


def restore_retention_cleanup_delete_window(apps, schema_editor):
    """Allow scheduled retention cleanup to delete expired logs again.

    Logs remain append-only during normal application/admin use:
    - UPDATE is still blocked by the existing update trigger.
    - DELETE is blocked unless the cleanup command sets the local
      djopenkb.audit_retention_cleanup session variable inside its transaction.
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(DELETE_FUNCTION_SQL)

        for table in AUDIT_TABLES:
            delete_trigger = f"{table}_block_delete"
            cursor.execute(f'DROP TRIGGER IF EXISTS {delete_trigger} ON "{table}";')
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
        ("kb", "0018_make_audit_logs_fully_immutable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="auth_activity_log_retention_days",
            field=models.PositiveIntegerField(
                default=30,
                help_text=(
                    "Authentication/MFA monitoring logs older than this many days can be deleted by the cleanup command. "
                    "Use 0 to keep authentication activity logs indefinitely."
                ),
                verbose_name="Authentication activity log retention (days)",
            ),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="activity_log_retention_days",
            field=models.PositiveIntegerField(
                default=30,
                help_text=(
                    "Article/vote/image/admin-tool activity logs older than this many days can be deleted by the cleanup command. "
                    "Use 0 to keep general activity logs indefinitely."
                ),
                verbose_name="General activity log retention (days)",
            ),
        ),
        migrations.RunPython(restore_retention_cleanup_delete_window, migrations.RunPython.noop),
    ]
