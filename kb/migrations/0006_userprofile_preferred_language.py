# Generated manually for per-user UI language selection.

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0005_sitesetting"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="preferred_language",
            field=models.CharField(
                choices=settings.LANGUAGES,
                default=settings.LANGUAGE_CODE,
                help_text="Preferred language for the main wiki user interface.",
                max_length=20,
            ),
        ),
    ]
