# Generated for configurable stray upload cleanup age.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0004_suggestedarticle_author_snapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "stray_upload_cleanup_min_age_minutes",
                    models.PositiveIntegerField(
                        default=30,
                        help_text=(
                            "Files newer than this many minutes are ignored by the stray upload cleanup tool. "
                            "Set to 0 to detect/delete stray uploads immediately."
                        ),
                        verbose_name="Stray upload cleanup minimum age (minutes)",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Site setting",
                "verbose_name_plural": "Site settings",
            },
        ),
    ]
