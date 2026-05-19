# Generated for DjOpenKB article translation cache.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0006_userprofile_preferred_language"),
    ]

    operations = [
        migrations.CreateModel(
            name="SuggestedArticleTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=20)),
                ("title", models.CharField(max_length=300)),
                ("body", models.TextField()),
                ("keywords", models.CharField(blank=True, max_length=700)),
                ("source_title_hash", models.CharField(blank=True, max_length=64)),
                ("source_body_hash", models.CharField(blank=True, max_length=64)),
                ("source_keywords_hash", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "article",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="kb.suggestedarticle",
                    ),
                ),
            ],
            options={
                "verbose_name": "Suggested Article Translation",
                "verbose_name_plural": "Suggested Article Translations",
                "unique_together": {("article", "language_code")},
            },
        ),
        migrations.AddIndex(
            model_name="suggestedarticletranslation",
            index=models.Index(fields=["article", "language_code"], name="kb_suggested_article_i18n_idx"),
        ),
    ]
