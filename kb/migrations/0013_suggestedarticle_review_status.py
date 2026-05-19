from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0012_suggestedarticle_pending_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="suggestedarticle",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending", "Pending"),
                    ("failed", "Pending failed"),
                    ("published", "Published"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Admin user who approved this article for public display.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_articles",
                verbose_name="Approved by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="approved_at",
            field=models.DateTimeField(
                verbose_name="Approved at",
                blank=True,
                help_text="Date and time when this article was approved for public display.",
                null=True,
            ),
        ),
    ]
