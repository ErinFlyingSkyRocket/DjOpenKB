# Generated for DjOpenKB suggested article/profile support.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SuggestedArticle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("keywords", models.CharField(blank=True, max_length=500)),
                ("status", models.CharField(choices=[("published", "Published"), ("draft", "Draft")], default="published", max_length=20)),
                ("filename", models.CharField(blank=True, max_length=255, unique=True)),
                ("raw_path", models.CharField(blank=True, max_length=500)),
                ("wiki_path", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="suggested_articles", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Suggested Article",
                "verbose_name_plural": "Suggested Articles",
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
    ]
