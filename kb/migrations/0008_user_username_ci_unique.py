# Enforce one case-insensitive Django username identity.

from django.conf import settings
from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower


USERNAME_INDEX_NAME = "auth_user_username_ci_unique"


def create_case_insensitive_username_index(apps, schema_editor):
    UserModel = apps.get_model(*settings.AUTH_USER_MODEL.split(".", 1))

    duplicates = list(
        UserModel.objects.annotate(username_ci=Lower("username"))
        .values("username_ci")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .order_by("username_ci")[:20]
    )
    if duplicates:
        duplicate_summary = ", ".join(
            f"{item['username_ci']} ({item['total']})" for item in duplicates
        )
        raise RuntimeError(
            "Cannot enforce case-insensitive username uniqueness because duplicate "
            f"usernames already exist: {duplicate_summary}. Rename or merge the "
            "duplicate User records in Django Admin, then run migrate again."
        )

    table_name = schema_editor.quote_name(UserModel._meta.db_table)
    username_column = schema_editor.quote_name(
        UserModel._meta.get_field("username").column
    )
    index_name = schema_editor.quote_name(USERNAME_INDEX_NAME)
    schema_editor.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
        f"ON {table_name} (LOWER({username_column}))"
    )


def drop_case_insensitive_username_index(apps, schema_editor):
    index_name = schema_editor.quote_name(USERNAME_INDEX_NAME)
    schema_editor.execute(f"DROP INDEX IF EXISTS {index_name}")


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0007_sitesetting_article_keyword_limit"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            create_case_insensitive_username_index,
            drop_case_insensitive_username_index,
        ),
    ]
