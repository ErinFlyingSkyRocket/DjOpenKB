from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0016_article_deletion_queue"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="article_deletion_queue_retention_days",
            field=models.PositiveIntegerField(
                default=7,
                help_text=(
                    "How long deleted published articles remain recoverable in My Profile → Admin tools → "
                    "Deletion queue before permanent deletion. Default is 7 days. Set to 0 to permanently "
                    "delete published articles immediately after MFA confirmation."
                ),
                verbose_name="Article deletion queue retention (days)",
            ),
        ),
    ]
