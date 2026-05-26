from django.db import migrations, models


def set_default_auth_log_retention(apps, schema_editor):
    SiteSetting = apps.get_model("kb", "SiteSetting")
    for setting in SiteSetting.objects.all():
        # Keep explicit custom values, but move older default deployments from 90 to 30 days.
        if getattr(setting, "auth_activity_log_retention_days", None) == 90:
            setting.auth_activity_log_retention_days = 30
            setting.save(update_fields=["auth_activity_log_retention_days"])


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0004_auth_activity_log_retention"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="auth_activity_log_retention_days",
            field=models.PositiveIntegerField(
                default=30,
                help_text="Authentication/MFA monitoring logs older than this many days can be deleted by the cleanup command. Use 0 to keep authentication activity logs indefinitely.",
                verbose_name="Authentication activity log retention (days)",
            ),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="session_timeout_days",
            field=models.PositiveIntegerField(
                default=30,
                help_text="Authenticated user sessions expire after this many days from sign-in. After expiry, users are signed out and must log in again. Use 0 to keep sessions until browser/session expiry.",
                verbose_name="User session timeout (days)",
            ),
        ),
        migrations.RunPython(set_default_auth_log_retention, migrations.RunPython.noop),
    ]
