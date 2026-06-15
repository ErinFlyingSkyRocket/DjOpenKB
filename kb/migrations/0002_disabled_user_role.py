from django.conf import settings
from django.db import migrations


DISABLED_ROLE_NAME = "Disabled User"


def create_disabled_user_role(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    group, _created = Group.objects.get_or_create(name=DISABLED_ROLE_NAME)
    # Disabled User intentionally has no permissions. The application checks
    # this role explicitly and stops the user after valid credentials/MFA.
    group.permissions.clear()


def reverse_noop(apps, schema_editor):
    # Keep the role on rollback so user access does not unexpectedly change.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_disabled_user_role, reverse_noop),
    ]
