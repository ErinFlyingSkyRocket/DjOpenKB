from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0008_user_username_ci_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="article_body_character_limit",
            field=models.PositiveIntegerField(
                default=200000,
                help_text=(
                    "Maximum number of characters allowed in an article body or pending article update. "
                    "Default is 200000. Allowed range: 1000 to 2000000."
                ),
                validators=[MinValueValidator(1000), MaxValueValidator(2000000)],
                verbose_name="Article body character limit",
            ),
        ),
    ]
