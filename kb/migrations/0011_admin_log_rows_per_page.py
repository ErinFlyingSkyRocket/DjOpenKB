from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0010_activity_log_retention_30_days"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="admin_log_rows_per_page",
            field=models.PositiveIntegerField(
                default=200,
                verbose_name="Admin log rows per page",
                help_text=(
                    "Number of rows to show per page in Django Admin log tables. "
                    "Recommended range: 50 to 500. Default is 200."
                ),
            ),
        ),
    ]
