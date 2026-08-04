from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


OLD_DEFAULT = 200_000
NEW_DEFAULT = 100_000


def apply_new_default_to_existing_site_setting(apps, schema_editor):
    """Move only the former default value to the new default.

    Values configured by an administrator to anything other than the former
    200,000-character default are preserved unchanged.
    """
    SiteSetting = apps.get_model("kb", "SiteSetting")
    SiteSetting.objects.filter(
        article_body_character_limit=OLD_DEFAULT
    ).update(article_body_character_limit=NEW_DEFAULT)


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0009_sitesetting_article_body_character_limit"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="article_body_character_limit",
            field=models.PositiveIntegerField(
                default=NEW_DEFAULT,
                help_text=(
                    "Maximum number of characters allowed in an article body or "
                    "pending article update. Default is 100000. Allowed range: "
                    "1000 to 2000000."
                ),
                validators=[MinValueValidator(1000), MaxValueValidator(2000000)],
                verbose_name="Article body character limit",
            ),
        ),
        migrations.RunPython(
            apply_new_default_to_existing_site_setting,
            migrations.RunPython.noop,
        ),
    ]
