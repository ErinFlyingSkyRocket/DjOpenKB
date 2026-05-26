from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0003_authactivitylog"),
    ]

    operations = [
        migrations.AddField(
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
    ]
