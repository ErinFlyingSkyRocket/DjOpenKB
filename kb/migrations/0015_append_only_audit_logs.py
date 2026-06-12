from django.db import migrations


TABLES = [
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
    IF COALESCE(current_setting('djopenkb.audit_retention_cleanup', true), '') = 'on' THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION 'Audit log rows cannot be manually deleted. They are removed only by retention cleanup.';
END;
$$ LANGUAGE plpgsql;
"""


def install_append_only_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(UPDATE_FUNCTION_SQL)
        cursor.execute(DELETE_FUNCTION_SQL)

        for table in TABLES:
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


def remove_append_only_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            update_trigger = f"{table}_block_update"
            delete_trigger = f"{table}_block_delete"
            cursor.execute(f'DROP TRIGGER IF EXISTS {update_trigger} ON "{table}";')
            cursor.execute(f'DROP TRIGGER IF EXISTS {delete_trigger} ON "{table}";')

        cursor.execute("DROP FUNCTION IF EXISTS kb_block_audit_log_update();")
        cursor.execute("DROP FUNCTION IF EXISTS kb_block_audit_log_delete_unless_retention_cleanup();")


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0014_article_image_upload_limit"),
    ]

    operations = [
        migrations.RunPython(install_append_only_triggers, remove_append_only_triggers),
    ]
