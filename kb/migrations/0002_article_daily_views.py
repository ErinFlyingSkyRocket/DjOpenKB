# Generated for daily unique authenticated article views.

from zoneinfo import ZoneInfo

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def seed_retained_daily_view_markers(apps, schema_editor):
    """Seed markers from retained view audit logs without changing totals.

    Existing ``view_count`` values were produced by the previous session-based
    implementation and are deliberately preserved. Seeding retained audit rows
    prevents an authenticated user who already generated a view log on the day
    of deployment from being counted again immediately after migration.
    """
    ActivityLog = apps.get_model("kb", "ActivityLog")
    ArticleDailyView = apps.get_model("kb", "ArticleDailyView")
    SuggestedArticle = apps.get_model("kb", "SuggestedArticle")

    article_ids = set(SuggestedArticle.objects.values_list("pk", flat=True))
    if not article_ids:
        return

    try:
        local_timezone = ZoneInfo(getattr(settings, "TIME_ZONE", "UTC") or "UTC")
    except Exception:
        local_timezone = ZoneInfo("UTC")

    rows = []
    seen = set()
    logs = ActivityLog.objects.filter(
        event_type="article_viewed",
        article_id__in=article_ids,
        user_id__isnull=False,
    ).values_list("article_id", "user_id", "created_at")

    for article_id, user_id, created_at in logs.iterator(chunk_size=1000):
        if created_at is None:
            continue
        if django.utils.timezone.is_aware(created_at):
            view_date = created_at.astimezone(local_timezone).date()
        else:
            view_date = created_at.date()

        key = (article_id, user_id, view_date)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            ArticleDailyView(
                article_id=article_id,
                user_id=user_id,
                view_date=view_date,
                created_at=created_at,
            )
        )

        if len(rows) >= 1000:
            ArticleDailyView.objects.bulk_create(rows, ignore_conflicts=True)
            rows.clear()

    if rows:
        ArticleDailyView.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="suggestedarticle",
            name="view_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="ArticleDailyView",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "view_date",
                    models.DateField(
                        db_index=True,
                        default=django.utils.timezone.localdate,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "article",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="daily_views",
                        to="kb.suggestedarticle",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="daily_article_views",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-view_date", "-created_at"),
                "indexes": [
                    models.Index(
                        fields=["article", "view_date"],
                        name="kb_artview_article_date_idx",
                    ),
                    models.Index(
                        fields=["user", "view_date"],
                        name="kb_artview_user_date_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("article", "user", "view_date"),
                        name="kb_art_daily_user_date_uniq",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            seed_retained_daily_view_markers,
            migrations.RunPython.noop,
        ),
    ]
