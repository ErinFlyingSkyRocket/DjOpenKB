# Generated for DjOpenKB article author snapshots.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_existing_author_details(apps, schema_editor):
    SuggestedArticle = apps.get_model("kb", "SuggestedArticle")
    UserProfile = apps.get_model("kb", "UserProfile")

    for article in SuggestedArticle.objects.select_related("owner").all():
        owner = article.owner
        if not owner:
            continue

        article.author_username_snapshot = owner.get_username()
        article.author_name_snapshot = owner.get_full_name().strip()
        article.author_email_snapshot = owner.email or ""

        try:
            profile = UserProfile.objects.get(user=owner)
            article.author_account_type_snapshot = profile.get_account_type_display()
        except UserProfile.DoesNotExist:
            if owner.is_superuser or owner.is_staff:
                article.author_account_type_snapshot = "Admin"
            else:
                article.author_account_type_snapshot = ""

        article.save(update_fields=[
            "author_username_snapshot",
            "author_name_snapshot",
            "author_email_snapshot",
            "author_account_type_snapshot",
        ])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0003_userprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestedarticle",
            name="author_account_type_snapshot",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="author_email_snapshot",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="author_name_snapshot",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="author_username_snapshot",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.RunPython(copy_existing_author_details, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="suggestedarticle",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="suggested_articles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
