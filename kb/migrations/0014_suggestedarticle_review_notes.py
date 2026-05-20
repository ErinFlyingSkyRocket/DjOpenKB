from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0013_suggestedarticle_review_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestedarticle",
            name="review_notes",
            field=models.TextField(
                blank=True,
                help_text="Admin feedback shown to the article owner when the article is in Draft or Pending failed status.",
                verbose_name="Pending failed comments",
            ),
        ),
    ]
