from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("kb", "0012_article_creation_workspace"),
    ]

    operations = [
        migrations.AddField(
            model_name="articlecreationworkspace",
            name="revision",
            field=models.PositiveBigIntegerField(
                default=0,
                help_text=(
                    "Optimistic-concurrency revision used to prevent an older browser tab "
                    "from overwriting a newer checkpoint."
                ),
            ),
        ),
        migrations.AddField(
            model_name="articlecreationworkspace",
            name="last_editor_token",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="articlecreationworkspace",
            name="last_editor_sequence",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="stray_upload_cleanup_min_age_minutes",
            field=models.PositiveIntegerField(
                default=1440,
                help_text=(
                    "Uploaded files newer than this many minutes are ignored when they are not "
                    "referenced by an article or a user-owned New Article checkpoint. Existing "
                    "checkpoints never expire because of age. Set to 0 only for immediate "
                    "orphan-file detection/deletion."
                ),
                verbose_name="Stray upload cleanup minimum age (minutes)",
            ),
        ),
    ]
