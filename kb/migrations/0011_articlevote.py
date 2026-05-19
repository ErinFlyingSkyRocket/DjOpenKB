# Generated manually for article helpful/unhelpful voting.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0010_drop_legacy_theme_preference_column"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ArticleVote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.SmallIntegerField(choices=[(1, "Helpful"), (-1, "Not helpful")])),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="votes", to="kb.suggestedarticle")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="article_votes", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Article vote",
                "verbose_name_plural": "Article votes",
                "ordering": ["-updated_at"],
                "unique_together": {("article", "user")},
            },
        ),
    ]
