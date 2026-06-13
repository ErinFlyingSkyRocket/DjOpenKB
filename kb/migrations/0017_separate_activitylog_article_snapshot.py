from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0016_activitylog_article_append_only_delete_fix"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="activitylog",
                    name="article",
                ),
                migrations.AddField(
                    model_name="activitylog",
                    name="article_id",
                    field=models.PositiveIntegerField(
                        blank=True,
                        db_index=True,
                        help_text="Historical article ID snapshot. This is intentionally not a live article relationship.",
                        null=True,
                        verbose_name="Article ID",
                    ),
                ),
            ],
        ),
    ]
