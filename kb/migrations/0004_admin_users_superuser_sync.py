# Generated for DjOpenKB admin role hardening.

from django.db import migrations


ROLE_DISABLED_USER = "Disabled User"
ROLE_ADMIN_USERS = "Admin Users"
PERM_USE_ADMIN_TOOLS = "can_use_admin_tools"
ADMIN_ACCOUNT_TYPES = {"admin", "ldap_admin"}


def promote_admin_users_to_superuser(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    UserProfile = apps.get_model("kb", "UserProfile")

    try:
        disabled_group = Group.objects.filter(name=ROLE_DISABLED_USER).first()
        admin_group = Group.objects.filter(name=ROLE_ADMIN_USERS).first()
        admin_permission = Permission.objects.filter(
            content_type__app_label="kb",
            codename=PERM_USE_ADMIN_TOOLS,
        ).first()
    except Exception:
        return

    for user in User.objects.all().iterator():
        is_disabled = bool(disabled_group and user.groups.filter(pk=disabled_group.pk).exists())
        has_admin_group = bool(admin_group and user.groups.filter(pk=admin_group.pk).exists())
        has_direct_admin_perm = bool(
            admin_permission and user.user_permissions.filter(pk=admin_permission.pk).exists()
        )
        profile = UserProfile.objects.filter(user_id=user.pk).first()
        has_admin_profile = bool(profile and profile.account_type in ADMIN_ACCOUNT_TYPES)

        should_be_superuser = bool(
            not is_disabled
            and (user.is_superuser or has_admin_group or has_direct_admin_perm or has_admin_profile)
        )

        update_fields = []
        if user.is_staff != should_be_superuser:
            user.is_staff = should_be_superuser
            update_fields.append("is_staff")
        if user.is_superuser != should_be_superuser:
            user.is_superuser = should_be_superuser
            update_fields.append("is_superuser")
        if update_fields:
            user.save(update_fields=update_fields)

        if profile:
            profile_updates = []
            if is_disabled and profile.can_access_main_site:
                profile.can_access_main_site = False
                profile_updates.append("can_access_main_site")
            elif should_be_superuser and not profile.can_access_main_site:
                profile.can_access_main_site = True
                profile_updates.append("can_access_main_site")
            if profile_updates:
                profile_updates.append("updated_at")
                profile.save(update_fields=profile_updates)


def noop_reverse(apps, schema_editor):
    # Do not automatically demote admins on rollback; that could lock out the site owner.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0003_admin_activity_log_and_permissions"),
    ]

    operations = [
        migrations.RunPython(promote_admin_users_to_superuser, noop_reverse),
    ]
