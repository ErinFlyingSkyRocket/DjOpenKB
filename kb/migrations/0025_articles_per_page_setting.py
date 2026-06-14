# Generated for DjOpenKB single article page-size setting.

from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0024_admin_allowed_cidrs_and_schema_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="articles_per_page",
            field=models.PositiveIntegerField(
                default=10,
                verbose_name=_("Articles per page"),
                help_text=_(
                    "Number of published articles shown per page in search/results and in each homepage article column "
                    "such as Trending Topics, Most Liked, and Most Recent Articles. Recommended range: 5 to 100. Default is 10."
                ),
            ),
        ),
    ]
