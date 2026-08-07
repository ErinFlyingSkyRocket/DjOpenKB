from django.db import migrations, models
from django.db.models import Q


def backfill_review_submission_snapshots(apps, schema_editor):
    SuggestedArticle = apps.get_model("kb", "SuggestedArticle")

    queryset = SuggestedArticle.objects.filter(
        Q(status__in=["pending", "failed"])
        | Q(status="published", update_status__in=["pending", "failed"])
    )

    for article in queryset.iterator():
        if article.status == "published" and article.update_status in {"pending", "failed"}:
            snapshot = {
                "kind": "update",
                "title": article.pending_update_title or article.title or "",
                "body": article.pending_update_body or article.body or "",
                "keywords": article.pending_update_keywords or article.keywords or "",
                "visibility": article.visibility,
                "image_assets": list(article.pending_update_image_assets or []),
            }
        else:
            snapshot = {
                "kind": "article",
                "title": article.title or "",
                "body": article.body or "",
                "keywords": article.keywords or "",
                "visibility": article.visibility,
                "image_assets": list(article.image_assets or []),
            }

        article.review_submission_snapshot = snapshot
        article.save(update_fields=["review_submission_snapshot"])


class Migration(migrations.Migration):
    dependencies = [
        ("kb", "0017_simplify_article_runtime"),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestedarticle",
            name="review_submission_snapshot",
            field=models.JSONField(
                blank=True,
                default=dict,
                editable=False,
                help_text=(
                    "Server-owned copy of the latest version explicitly submitted by the article owner. "
                    "Reviewers may edit the shared pending copy and reset their review form back to this submitted version."
                ),
                verbose_name="User-submitted review snapshot",
            ),
        ),
        migrations.RunPython(
            backfill_review_submission_snapshots,
            migrations.RunPython.noop,
        ),
    ]
