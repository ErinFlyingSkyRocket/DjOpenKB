from django.db import migrations


ROLE_ADMIN_USERS = "Admin Users"
ROLE_DISABLED_USER = "Disabled User"
AUTH_SOURCE_AD = "ad"
ACCOUNT_TYPE_ADMIN = "admin"
ACCOUNT_TYPE_USER = "user"
ACCOUNT_TYPE_LDAP_USER = "ldap_user"
ACCOUNT_TYPE_LDAP_ADMIN = "ldap_admin"


def normalise_account_status(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")
    UserProfile = apps.get_model("kb", "UserProfile")

    admin_group = Group.objects.filter(name=ROLE_ADMIN_USERS).first()
    disabled_group = Group.objects.filter(name=ROLE_DISABLED_USER).first()

    for user in User.objects.all().iterator():
        profile, _created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "account_type": ACCOUNT_TYPE_USER,
                "auth_source": "local",
                "can_access_main_site": True,
                "preferred_language": "en",
            },
        )

        has_admin_group = bool(admin_group and user.groups.filter(pk=admin_group.pk).exists())
        has_disabled_group = bool(disabled_group and user.groups.filter(pk=disabled_group.pk).exists())
        is_ldap = profile.auth_source == AUTH_SOURCE_AD

        if has_disabled_group:
            target_type = ACCOUNT_TYPE_LDAP_USER if is_ldap else ACCOUNT_TYPE_USER
            target_staff = False
            target_superuser = False
        elif has_admin_group:
            target_type = ACCOUNT_TYPE_LDAP_ADMIN if is_ldap else ACCOUNT_TYPE_ADMIN
            target_staff = True
            target_superuser = True
        else:
            target_type = ACCOUNT_TYPE_LDAP_USER if is_ldap else ACCOUNT_TYPE_USER
            target_staff = False
            target_superuser = False

        profile_changed = []
        if profile.account_type != target_type:
            profile.account_type = target_type
            profile_changed.append("account_type")
        # Retire can_access_main_site as an admin control. User.is_active is now
        # the actual active/inactive sign-in flag; keep this legacy flag enabled
        # so it cannot conflict with the Disabled User redirect flow.
        if not profile.can_access_main_site:
            profile.can_access_main_site = True
            profile_changed.append("can_access_main_site")
        if profile_changed:
            profile_changed.append("updated_at")
            profile.save(update_fields=profile_changed)

        user_changed = []
        if user.is_staff != target_staff:
            user.is_staff = target_staff
            user_changed.append("is_staff")
        if user.is_superuser != target_superuser:
            user.is_superuser = target_superuser
            user_changed.append("is_superuser")
        if user_changed:
            user.save(update_fields=user_changed)


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0006_admin_users_exclusive_and_profile_sync"),
    ]

    operations = [
        migrations.RunPython(normalise_account_status, migrations.RunPython.noop),
    ]
