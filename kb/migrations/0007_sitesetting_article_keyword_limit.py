# Generated for the configurable per-article keyword limit.

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0006_request_rate_limits_and_unique_user_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="article_keyword_limit",
            field=models.PositiveIntegerField(
                default=20,
                help_text=(
                    "Maximum number of keywords allowed for each article and pending article update. "
                    "Default is 20. Allowed range: 1 to 100."
                ),
                validators=[MinValueValidator(1), MaxValueValidator(100)],
                verbose_name="Article keyword limit",
            ),
        ),
    ]
