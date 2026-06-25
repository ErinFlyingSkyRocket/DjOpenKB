from django.conf import settings
from django.db import migrations


PUBLIC_MANAGER = "Article Manager"
PUBLIC_LOWER_ROLES = ("Article Writer", "Article Approver")
INTERNAL_MANAGER = "Internal Article Manager"
INTERNAL_LOWER_ROLES = (
    "Internal User",
    "Internal Article Writer",
    "Internal Article Approver",
)


def remove_lower_manager_roles(apps, schema_editor):
    """Apply Manager precedence to existing users once at deployment time."""
    Group = apps.get_model("auth", "Group")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)

    role_sets = (
        (PUBLIC_MANAGER, PUBLIC_LOWER_ROLES),
        (INTERNAL_MANAGER, INTERNAL_LOWER_ROLES),
    )

    for manager_name, lower_role_names in role_sets:
        manager_group = Group.objects.filter(name=manager_name).first()
        if manager_group is None:
            continue
        lower_groups = list(Group.objects.filter(name__in=lower_role_names))
        if not lower_groups:
            continue

        for user in User.objects.filter(groups=manager_group).distinct():
            user.groups.remove(*lower_groups)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0019_openkb_ai_prompt_limit_per_24_hours"),
    ]

    operations = [
        migrations.RunPython(remove_lower_manager_roles, migrations.RunPython.noop),
    ]
