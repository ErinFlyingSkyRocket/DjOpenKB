from django.db import migrations


ADMIN_ROLE_NAME = "Admin Users"
ADMIN_PERMISSION_CODENAME = "can_use_admin_tools"


def remove_redundant_admin_view_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    try:
        admin_group = Group.objects.get(name=ADMIN_ROLE_NAME)
    except Group.DoesNotExist:
        return

    keep_permissions = Permission.objects.filter(
        content_type__app_label="kb",
        content_type__model="suggestedarticle",
        codename__in=[
            "can_view_articles",
            "can_add_articles",
            "can_manage_articles",
            ADMIN_PERMISSION_CODENAME,
        ],
    )
    admin_group.permissions.set(keep_permissions)

    direct_admin_permission = Permission.objects.filter(
        content_type__app_label="kb",
        content_type__model="suggestedarticle",
        codename=ADMIN_PERMISSION_CODENAME,
    ).first()
    if direct_admin_permission is not None:
        # Existing direct grants of "can use admin tools" are no longer used for
        # Django Admin/superuser access. Remove them to avoid confusing admin UI
        # state; proper admin access is controlled by Admin Users/superuser.
        direct_admin_permission.user_set.clear()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0004_admin_users_superuser_sync"),
    ]

    operations = [
        migrations.RunPython(remove_redundant_admin_view_permissions, noop_reverse),
    ]
