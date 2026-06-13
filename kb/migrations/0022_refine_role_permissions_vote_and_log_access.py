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


def _model_permissions(Permission, app_label, model, actions):
    return list(
        Permission.objects.filter(
            content_type__app_label=app_label,
            content_type__model=model,
            codename__in=[f"{action}_{model}" for action in actions],
        )
    )


def refine_role_permissions(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")

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

    for role_name, codenames in ROLE_GROUPS.items():
        group, _ = Group.objects.get_or_create(name=role_name)
        permissions = [custom_permission_map[codename] for codename in codenames]

        if role_name == "Article Manager":
            permissions.extend(_model_permissions(Permission, "kb", "suggestedarticle", {"view", "change"}))
            permissions.extend(_model_permissions(Permission, "kb", "activitylog", {"view"}))
            permissions.extend(_model_permissions(Permission, "kb", "authactivitylog", {"view"}))
            permissions.extend(_model_permissions(Permission, "kb", "articleimageuploadlog", {"view"}))

        if role_name == "Admin Users":
            # Full DjOpenKB role/admin capability, but audit log tables are view-only.
            permissions.extend(_model_permissions(Permission, "kb", "suggestedarticle", {"view", "add", "change", "delete"}))
            permissions.extend(_model_permissions(Permission, "kb", "articlevote", {"view", "change", "delete"}))
            permissions.extend(_model_permissions(Permission, "kb", "sitesetting", {"view", "change"}))
            permissions.extend(_model_permissions(Permission, "kb", "userprofile", {"view", "change"}))
            permissions.extend(_model_permissions(Permission, "kb", "usermfadevice", {"view", "change"}))
            permissions.extend(_model_permissions(Permission, "kb", "activitylog", {"view"}))
            permissions.extend(_model_permissions(Permission, "kb", "authactivitylog", {"view"}))
            permissions.extend(_model_permissions(Permission, "kb", "articleimageuploadlog", {"view"}))
            permissions.extend(
                Permission.objects.filter(
                    content_type__app_label="auth",
                    content_type__model__in=("user", "group"),
                )
            )

        group.permissions.set(sorted(set(permissions), key=lambda permission: permission.pk))


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0021_article_role_permissions_groups"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(refine_role_permissions, reverse_noop),
    ]
