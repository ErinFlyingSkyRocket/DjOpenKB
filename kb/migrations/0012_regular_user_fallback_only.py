from django.db import migrations


ROLE_DISABLED_USER = "Disabled User"
ROLE_REGULAR_USER = "Regular User"
ROLE_ARTICLE_WRITER = "Article Writer"
ROLE_ARTICLE_APPROVER = "Article Approver"
ROLE_ARTICLE_MANAGER = "Article Manager"
ROLE_ADMIN_USERS = "Admin Users"

ELEVATED_OR_OVERRIDE_ROLES = (
    ROLE_DISABLED_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)


def remove_redundant_regular_user(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")

    regular = Group.objects.filter(name=ROLE_REGULAR_USER).first()
    if regular is None:
        return

    elevated_group_ids = list(
        Group.objects.filter(name__in=ELEVATED_OR_OVERRIDE_ROLES).values_list("id", flat=True)
    )
    if not elevated_group_ids:
        return

    users = User.objects.filter(groups=regular).filter(groups__in=elevated_group_ids).distinct()
    for user in users.iterator():
        user.groups.remove(regular)


def noop_reverse(apps, schema_editor):
    # Do not re-add Regular User on reverse. The application will add it when an
    # account has no standard Knowledge Repository role at all.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0011_article_approver_and_article_manager_role"),
    ]

    operations = [
        migrations.RunPython(remove_redundant_regular_user, noop_reverse),
    ]
