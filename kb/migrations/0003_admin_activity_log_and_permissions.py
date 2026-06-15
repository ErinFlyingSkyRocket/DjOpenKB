# Generated for DjOpenKB/Knowledge Repository admin audit hardening.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


ADMIN_AUDIT_TABLE = "kb_adminactivitylog"

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


def install_admin_activity_log_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(UPDATE_FUNCTION_SQL)
        cursor.execute(DELETE_FUNCTION_SQL)
        cursor.execute(f'DROP TRIGGER IF EXISTS {ADMIN_AUDIT_TABLE}_block_update ON "{ADMIN_AUDIT_TABLE}";')
        cursor.execute(f'DROP TRIGGER IF EXISTS {ADMIN_AUDIT_TABLE}_block_delete ON "{ADMIN_AUDIT_TABLE}";')
        cursor.execute(
            f'''
            CREATE TRIGGER {ADMIN_AUDIT_TABLE}_block_update
            BEFORE UPDATE ON "{ADMIN_AUDIT_TABLE}"
            FOR EACH ROW
            EXECUTE FUNCTION kb_block_audit_log_update();
            '''
        )
        cursor.execute(
            f'''
            CREATE TRIGGER {ADMIN_AUDIT_TABLE}_block_delete
            BEFORE DELETE ON "{ADMIN_AUDIT_TABLE}"
            FOR EACH ROW
            EXECUTE FUNCTION kb_block_audit_log_delete_unless_retention_cleanup();
            '''
        )


def remove_admin_activity_log_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'DROP TRIGGER IF EXISTS {ADMIN_AUDIT_TABLE}_block_update ON "{ADMIN_AUDIT_TABLE}";')
        cursor.execute(f'DROP TRIGGER IF EXISTS {ADMIN_AUDIT_TABLE}_block_delete ON "{ADMIN_AUDIT_TABLE}";')


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0002_disabled_user_role"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminActivityLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("event_type", models.CharField(choices=[("admin_add", "Admin object created"), ("admin_change", "Admin object changed"), ("admin_delete", "Admin object deleted"), ("admin_action", "Admin action/request")], db_index=True, max_length=40)),
                ("admin_username", models.CharField(blank=True, db_index=True, max_length=255)),
                ("target_app_label", models.CharField(blank=True, db_index=True, max_length=100)),
                ("target_model", models.CharField(blank=True, db_index=True, max_length=100)),
                ("target_object_id", models.CharField(blank=True, db_index=True, max_length=255)),
                ("target_repr", models.CharField(blank=True, max_length=500)),
                ("action_flag", models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, db_index=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                ("path", models.CharField(blank=True, max_length=500)),
                ("request_method", models.CharField(blank=True, max_length=10)),
                ("status_code", models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ("change_message", models.TextField(blank=True)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("admin_user", models.ForeignKey(blank=True, db_constraint=False, help_text="Historical admin user snapshot only. This relation intentionally does not enforce a database constraint because admin audit logs must remain immutable when users are deleted.", null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="admin_activity_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Admin activity log",
                "verbose_name_plural": "Admin activity logs",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["-created_at", "event_type"], name="kb_adminlog_created_idx"),
                    models.Index(fields=["admin_username", "-created_at"], name="kb_adminlog_user_idx"),
                    models.Index(fields=["target_app_label", "target_model", "-created_at"], name="kb_adminlog_target_idx"),
                    models.Index(fields=["ip_address", "-created_at"], name="kb_adminlog_ip_idx"),
                ],
            },
        ),
        migrations.RunPython(install_admin_activity_log_trigger, remove_admin_activity_log_trigger),
    ]
