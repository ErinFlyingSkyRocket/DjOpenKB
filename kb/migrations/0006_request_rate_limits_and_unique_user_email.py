# Generated for configurable request limits and global case-insensitive email uniqueness.

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower


EMAIL_INDEX_NAME = "auth_user_email_ci_unique_nonblank"


def create_case_insensitive_email_index(apps, schema_editor):
    UserModel = apps.get_model(*settings.AUTH_USER_MODEL.split(".", 1))

    duplicates = list(
        UserModel.objects.exclude(email="")
        .annotate(email_ci=Lower("email"))
        .values("email_ci")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .order_by("email_ci")[:20]
    )
    if duplicates:
        duplicate_summary = ", ".join(
            f"{item['email_ci']} ({item['total']})" for item in duplicates
        )
        raise RuntimeError(
            "Cannot enforce case-insensitive email uniqueness because duplicate "
            f"non-blank emails already exist: {duplicate_summary}. Resolve the "
            "duplicate User records in Django Admin, then run migrate again."
        )

    table_name = schema_editor.quote_name(UserModel._meta.db_table)
    email_column = schema_editor.quote_name(
        UserModel._meta.get_field("email").column
    )
    index_name = schema_editor.quote_name(EMAIL_INDEX_NAME)
    schema_editor.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
        f"ON {table_name} (LOWER({email_column})) "
        f"WHERE {email_column} <> ''"
    )


def drop_case_insensitive_email_index(apps, schema_editor):
    index_name = schema_editor.quote_name(EMAIL_INDEX_NAME)
    schema_editor.execute(f"DROP INDEX IF EXISTS {index_name}")


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0005_sitesetting_admin_mfa_verification_timeout_seconds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="login_request_limit_per_minute",
            field=models.PositiveIntegerField(
                default=8,
                help_text=(
                    "Application-side Redis request limit for username/password submissions from one IP address. "
                    "Default is 8 per minute. Set to 0 to disable this application-side limit. Allowed range: 0 to 120."
                ),
                validators=[MinValueValidator(0), MaxValueValidator(120)],
                verbose_name="Login POST requests per IP per minute",
            ),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="mfa_request_limit_per_minute",
            field=models.PositiveIntegerField(
                default=10,
                help_text=(
                    "Application-side Redis request limit shared by main-login MFA and Admin MFA submissions from one IP address. "
                    "Default is 10 per minute. Set to 0 to disable this application-side limit. Allowed range: 0 to 120."
                ),
                validators=[MinValueValidator(0), MaxValueValidator(120)],
                verbose_name="MFA POST requests per IP per minute",
            ),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="admin_request_limit_per_minute",
            field=models.PositiveIntegerField(
                default=120,
                help_text=(
                    "Application-side Redis request limit for ordinary Django Admin changes after Admin MFA succeeds. "
                    "The counter is per signed-in administrator, not shared by everyone behind the same office IP. "
                    "Default is 120 per minute. Set to 0 to disable this application-side limit. Allowed range: 0 to 600."
                ),
                validators=[MinValueValidator(0), MaxValueValidator(600)],
                verbose_name="Django Admin POST requests per administrator per minute",
            ),
        ),
        migrations.RunPython(
            create_case_insensitive_email_index,
            drop_case_insensitive_email_index,
        ),
    ]
