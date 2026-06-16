from django.db import migrations


ROLE_DISABLED_USER = "Disabled User"
ROLE_REGULAR_USER = "Regular User"
ROLE_ARTICLE_WRITER = "Article Writer"
ROLE_ARTICLE_MANAGER = "Article Manager"
ROLE_ADMIN_USERS = "Admin Users"
ROLE_ACCESS_GROUPS = (
    ROLE_REGULAR_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)
AUTH_SOURCE_AD = "ad"
ACCOUNT_TYPE_USER = "user"
ACCOUNT_TYPE_LDAP_USER = "ldap_user"


def enforce_disabled_user_precedence(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    UserProfile = apps.get_model("kb", "UserProfile")

    disabled_group = Group.objects.filter(name=ROLE_DISABLED_USER).first()
    if not disabled_group:
        return

    access_groups = list(Group.objects.filter(name__in=ROLE_ACCESS_GROUPS))
    kb_permissions = list(Permission.objects.filter(content_type__app_label="kb"))

    disabled_users = User.objects.filter(groups=disabled_group).distinct()
    for user in disabled_users.iterator():
        if access_groups:
            user.groups.remove(*access_groups)
        if kb_permissions:
            user.user_permissions.remove(*kb_permissions)

        changed_user_fields = []
        if user.is_staff:
            user.is_staff = False
            changed_user_fields.append("is_staff")
        if user.is_superuser:
            user.is_superuser = False
            changed_user_fields.append("is_superuser")
        if changed_user_fields:
            user.save(update_fields=changed_user_fields)

        profile, _created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "account_type": ACCOUNT_TYPE_USER,
                "auth_source": "local",
                "can_access_main_site": True,
                "preferred_language": "en",
            },
        )
        target_type = ACCOUNT_TYPE_LDAP_USER if profile.auth_source == AUTH_SOURCE_AD else ACCOUNT_TYPE_USER
        profile_changed = []
        if profile.account_type != target_type:
            profile.account_type = target_type
            profile_changed.append("account_type")
        if not profile.can_access_main_site:
            profile.can_access_main_site = True
            profile_changed.append("can_access_main_site")
        if profile_changed:
            profile_changed.append("updated_at")
            profile.save(update_fields=profile_changed)


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0007_normalise_account_status_from_roles"),
    ]

    operations = [
        migrations.RunPython(enforce_disabled_user_precedence, migrations.RunPython.noop),
    ]
