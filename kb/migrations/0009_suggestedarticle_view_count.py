# Generated for DjOpenKB article view counter

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0008_remove_suggestedarticletranslation"),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestedarticle",
            name="view_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Number of unique session views for this article.",
            ),
        ),
    ]
