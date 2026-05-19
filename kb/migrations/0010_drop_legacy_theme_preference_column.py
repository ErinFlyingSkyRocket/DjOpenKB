from django.db import migrations


class Migration(migrations.Migration):
    """Remove the old dark-mode/theme preference database column if it exists.

    Some earlier theme experiments created kb_userprofile.theme_preference in the
    database. The current application no longer uses that field, but PostgreSQL
    may still enforce its NOT NULL constraint when admin creates a new user.
    This migration safely drops the legacy column without changing the current
    Django model state.
    """

    dependencies = [
        ("kb", "0009_suggestedarticle_view_count"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE kb_userprofile "
                "DROP COLUMN IF EXISTS theme_preference;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
