# Generated for Knowledge Repository role split.

from django.db import migrations


PERM_VIEW_ARTICLES = "can_view_articles"
PERM_ADD_ARTICLES = "can_add_articles"
PERM_MANAGE_ARTICLES = "can_manage_articles"
PERM_DELETE_ARTICLES = "can_delete_articles"
PERM_USE_ADMIN_TOOLS = "can_use_admin_tools"

ROLE_DISABLED_USER = "Disabled User"
ROLE_REGULAR_USER = "Regular User"
ROLE_ARTICLE_WRITER = "Article Writer"
ROLE_ARTICLE_APPROVER = "Article Approver"
ROLE_ARTICLE_MANAGER = "Article Manager"
ROLE_ADMIN_USERS = "Admin Users"

PERMISSION_LABELS = {
    PERM_VIEW_ARTICLES: "Can view published articles",
    PERM_ADD_ARTICLES: "Can add/submit articles for approval",
    PERM_MANAGE_ARTICLES: "Can approve/manage pending article reviews",
    PERM_DELETE_ARTICLES: "Can delete articles",
    PERM_USE_ADMIN_TOOLS: "Can use Knowledge Repository admin tools",
}

ROLE_DEFINITIONS = {
    ROLE_DISABLED_USER: (),
    ROLE_REGULAR_USER: (PERM_VIEW_ARTICLES,),
    ROLE_ARTICLE_WRITER: (PERM_VIEW_ARTICLES, PERM_ADD_ARTICLES),
    ROLE_ARTICLE_APPROVER: (PERM_VIEW_ARTICLES, PERM_MANAGE_ARTICLES),
    ROLE_ARTICLE_MANAGER: (PERM_VIEW_ARTICLES, PERM_ADD_ARTICLES, PERM_MANAGE_ARTICLES, PERM_DELETE_ARTICLES),
    ROLE_ADMIN_USERS: (
        PERM_VIEW_ARTICLES,
        PERM_ADD_ARTICLES,
        PERM_MANAGE_ARTICLES,
        PERM_DELETE_ARTICLES,
        PERM_USE_ADMIN_TOOLS,
    ),
}


def forwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    SuggestedArticle = apps.get_model("kb", "SuggestedArticle")

    content_type = ContentType.objects.get_for_model(SuggestedArticle)

    permissions = {}
    for codename, label in PERMISSION_LABELS.items():
        permission, _created = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": label},
        )
        if permission.name != label:
            permission.name = label
            permission.save(update_fields=["name"])
        permissions[codename] = permission

    # Preserve the old Article Manager users as Article Approvers because the
    # old role only handled pending-article approval/review. The recreated
    # Article Manager role below is the new full article-management role.
    manager_group = Group.objects.filter(name=ROLE_ARTICLE_MANAGER).first()
    approver_group = Group.objects.filter(name=ROLE_ARTICLE_APPROVER).first()
    if manager_group and not approver_group:
        manager_group.name = ROLE_ARTICLE_APPROVER
        manager_group.save(update_fields=["name"])
    elif manager_group and approver_group:
        approver_group.user_set.add(*manager_group.user_set.all())

    for role_name, perm_codenames in ROLE_DEFINITIONS.items():
        group, _created = Group.objects.get_or_create(name=role_name)
        group.permissions.set([permissions[codename] for codename in perm_codenames])


def backwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    SuggestedArticle = apps.get_model("kb", "SuggestedArticle")

    content_type = ContentType.objects.get_for_model(SuggestedArticle)
    permissions = {
        permission.codename: permission
        for permission in Permission.objects.filter(content_type=content_type)
    }

    approver_group = Group.objects.filter(name=ROLE_ARTICLE_APPROVER).first()
    manager_group = Group.objects.filter(name=ROLE_ARTICLE_MANAGER).first()
    if approver_group and manager_group:
        manager_group.user_set.add(*approver_group.user_set.all())
        approver_group.delete()
    elif approver_group and not manager_group:
        approver_group.name = ROLE_ARTICLE_MANAGER
        approver_group.save(update_fields=["name"])

    legacy_manager = Group.objects.filter(name=ROLE_ARTICLE_MANAGER).first()
    if legacy_manager:
        legacy_perms = [
            permissions[codename]
            for codename in (PERM_VIEW_ARTICLES, PERM_MANAGE_ARTICLES)
            if codename in permissions
        ]
        legacy_manager.permissions.set(legacy_perms)


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0010_admin_mfa_idle_timeout_default_10min"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
