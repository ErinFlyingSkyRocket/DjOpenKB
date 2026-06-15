# Generated for DjOpenKB authentication lockout policy simplification.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0027_simplify_auth_lockout_default_policy"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="authlockoutpolicystage",
            name="failure_window_seconds",
        ),
    ]
