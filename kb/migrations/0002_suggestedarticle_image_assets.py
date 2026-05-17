# Generated for DjOpenKB image asset tracking

import re

from django.db import migrations, models


ARTICLE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(/wiki/uploads/([A-Za-z0-9._-]+)\)")


def populate_existing_image_assets(apps, schema_editor):
    SuggestedArticle = apps.get_model("kb", "SuggestedArticle")
    for article in SuggestedArticle.objects.all():
        seen = []
        for filename in ARTICLE_IMAGE_RE.findall(article.body or ""):
            if filename not in seen and "/" not in filename and "\\" not in filename:
                seen.append(filename)
        if seen:
            article.image_assets = seen
            article.save(update_fields=["image_assets"])


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestedarticle",
            name="image_assets",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(populate_existing_image_assets, migrations.RunPython.noop),
    ]
