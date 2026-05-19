# Generated manually to remove the unused AI translation cache model.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0007_suggestedarticletranslation"),
    ]

    operations = [
        migrations.DeleteModel(
            name="SuggestedArticleTranslation",
        ),
    ]
