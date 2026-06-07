from django.db import migrations, models


def set_default_activity_log_retention_to_30(apps, schema_editor):
    SiteSetting = apps.get_model("kb", "SiteSetting")
    # Preserve deliberate custom values. Only convert the old default value.
    SiteSetting.objects.filter(activity_log_retention_days=90).update(activity_log_retention_days=30)


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0009_activity_log"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="activity_log_retention_days",
            field=models.PositiveIntegerField(
                default=30,
                verbose_name="General activity log retention (days)",
                help_text="Number of days to keep general activity logs. Set to 0 to keep forever.",
            ),
        ),
        migrations.RunPython(set_default_activity_log_retention_to_30, migrations.RunPython.noop),
    ]
