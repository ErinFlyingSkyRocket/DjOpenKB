from django.db import migrations


ACCOUNT_DELETE_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION kb_block_audit_log_delete_unless_retention_cleanup()
RETURNS trigger AS $$
BEGIN
    IF COALESCE(current_setting('djopenkb.audit_retention_cleanup', true), '') = 'on'
       OR COALESCE(current_setting('djopenkb.account_deletion_cleanup', true), '') = 'on' THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION 'Audit log rows cannot be manually deleted. They are removed only by retention or protected account-deletion cleanup.';
END;
$$ LANGUAGE plpgsql;
"""

RETENTION_ONLY_FUNCTION_SQL = r"""
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


def enable_account_deletion_cleanup(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(ACCOUNT_DELETE_FUNCTION_SQL)


def restore_retention_only_cleanup(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(RETENTION_ONLY_FUNCTION_SQL)


class Migration(migrations.Migration):
    dependencies = [
        ("kb", "0013_persistent_article_workspace_revisions"),
    ]

    operations = [
        migrations.RunPython(
            enable_account_deletion_cleanup,
            restore_retention_only_cleanup,
        ),
    ]
