# Generated for DjOpenKB article image upload limit setting.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0013_article_pending_update"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="article_image_upload_limit",
            field=models.PositiveIntegerField(
                default=100,
                help_text=(
                    "Maximum number of pasted/uploaded images allowed per article, including draft, "
                    "pending, published, and pending-update versions. Default is 100. Set to 0 to disable article image uploads."
                ),
                verbose_name="Article image upload limit",
            ),
        ),
    ]
