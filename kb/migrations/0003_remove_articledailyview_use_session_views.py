# Switch article view uniqueness from permanent daily rows to Django sessions.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0002_article_daily_views"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ArticleDailyView",
        ),
    ]
