from django.db import migrations, models
import django.core.validators


def normalize_title(value):
    import re

    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def populate_normalized_titles(apps, schema_editor):
    SuggestedArticle = apps.get_model("kb", "SuggestedArticle")
    seen = {}
    duplicates = []

    for article in SuggestedArticle.objects.all().order_by("pk").only("pk", "title"):
        normalized = normalize_title(article.title)
        if not normalized:
            duplicates.append(f"article ID {article.pk} has an empty title")
            continue
        if normalized in seen:
            duplicates.append(
                f"article IDs {seen[normalized]} and {article.pk} normalize to the same title"
            )
            continue
        seen[normalized] = article.pk
        SuggestedArticle.objects.filter(pk=article.pk).update(normalized_title=normalized)

    if duplicates:
        preview = "; ".join(duplicates[:10])
        raise RuntimeError(
            "Cannot add the database article-title uniqueness constraint because "
            f"existing article data contains conflicts: {preview}. Rename the "
            "conflicting article titles and run the migration again."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("kb", "0010_reduce_default_article_body_character_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="pending_image_upload_byte_limit_mb_per_user",
            field=models.PositiveIntegerField(
                default=100,
                help_text="Maximum combined storage for one user's uncommitted article images across all browsers and sessions. Default is 100 MB. Allowed range: 1 to 2048 MB.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(2048),
                ],
                verbose_name="Pending image upload storage per user (MB)",
            ),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="pending_image_upload_limit_per_user",
            field=models.PositiveIntegerField(
                default=100,
                help_text="Maximum number of uncommitted article images one user may keep across all browsers and sessions. Default is 100. Allowed range: 1 to 1000.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(1000),
                ],
                verbose_name="Pending image uploads per user",
            ),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="normalized_title",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=600,
                null=True,
                verbose_name="Normalized article title",
            ),
        ),
        migrations.RunPython(populate_normalized_titles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="suggestedarticle",
            name="normalized_title",
            field=models.CharField(
                editable=False,
                help_text="Internal case-insensitive title key used to prevent duplicate article titles.",
                max_length=600,
                unique=True,
                verbose_name="Normalized article title",
            ),
        ),
    ]
