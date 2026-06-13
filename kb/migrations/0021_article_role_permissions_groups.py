from django.conf import settings
from django.db import migrations


CUSTOM_PERMISSIONS = {
    "can_view_articles": "Can view published articles",
    "can_add_articles": "Can add/submit articles for approval",
    "can_manage_articles": "Can manage pending articles and article reviews",
    "can_use_admin_tools": "Can use DjOpenKB admin tools",
}

ROLE_GROUPS = {
    "Regular User": ("can_view_articles",),
    "Article Writer": ("can_view_articles", "can_add_articles"),
    "Article Manager": ("can_view_articles", "can_manage_articles"),
    "Admin Users": (
        "can_view_articles",
        "can_add_articles",
        "can_manage_articles",
        "can_use_admin_tools",
    ),
}

ROLE_GROUP_NAMES = tuple(ROLE_GROUPS.keys())


def create_article_role_permissions_and_groups(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("kb", "UserProfile")

    article_ct, _ = ContentType.objects.get_or_create(app_label="kb", model="suggestedarticle")

    custom_permission_map = {}
    for codename, name in CUSTOM_PERMISSIONS.items():
        permission, _ = Permission.objects.get_or_create(
            content_type=article_ct,
            codename=codename,
            defaults={"name": name},
        )
        if permission.name != name:
            permission.name = name
            permission.save(update_fields=["name"])
        custom_permission_map[codename] = permission

    group_objects = {}
    for role_name, codenames in ROLE_GROUPS.items():
        group, _ = Group.objects.get_or_create(name=role_name)
        permissions = [custom_permission_map[codename] for codename in codenames]

        if role_name == "Article Manager":
            permissions.extend(
                Permission.objects.filter(
                    content_type__app_label="kb",
                    codename__in=("view_suggestedarticle", "change_suggestedarticle"),
                )
            )

        if role_name == "Admin Users":
            permissions.extend(Permission.objects.filter(content_type__app_label="kb"))
            permissions.extend(
                Permission.objects.filter(
                    content_type__app_label="auth",
                    content_type__model__in=("user", "group"),
                )
            )

        group.permissions.set(sorted(set(permissions), key=lambda permission: permission.pk))
        group_objects[role_name] = group

    role_group_qs = Group.objects.filter(name__in=ROLE_GROUP_NAMES)
    for user in User.objects.all():
        if user.groups.filter(name__in=ROLE_GROUP_NAMES).exists():
            continue

        profile = UserProfile.objects.filter(user_id=user.pk).first()
        account_type = getattr(profile, "account_type", "") or ""
        if user.is_superuser or user.is_staff or account_type in {"admin", "ldap_admin"}:
            role_name = "Admin Users"
        else:
            role_name = "Regular User"

        user.groups.add(group_objects[role_name])
        if role_name == "Admin Users" and not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])


def reverse_noop(apps, schema_editor):
    # Keep groups/permissions if this migration is rolled back. Removing them
    # could unexpectedly lock admins out of the site.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0020_activitylog_article_owner_snapshot"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="suggestedarticle",
            options={
                "ordering": ["-updated_at", "-created_at"],
                "verbose_name": "Suggested Article",
                "verbose_name_plural": "Suggested Articles",
                "permissions": [
                    ("can_view_articles", "Can view published articles"),
                    ("can_add_articles", "Can add/submit articles for approval"),
                    ("can_manage_articles", "Can manage pending articles and article reviews"),
                    ("can_use_admin_tools", "Can use DjOpenKB admin tools"),
                ],
            },
        ),
        migrations.RunPython(create_article_role_permissions_and_groups, reverse_noop),
    ]
