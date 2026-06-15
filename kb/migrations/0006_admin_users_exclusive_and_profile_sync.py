from django.db import migrations


ROLE_DISABLED_USER = "Disabled User"
ROLE_REGULAR_USER = "Regular User"
ROLE_ARTICLE_WRITER = "Article Writer"
ROLE_ARTICLE_MANAGER = "Article Manager"
ROLE_ADMIN_USERS = "Admin Users"
ROLE_ACCESS_GROUPS = [
    ROLE_REGULAR_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
]
NON_ADMIN_ROLE_GROUPS = [ROLE_REGULAR_USER, ROLE_ARTICLE_WRITER, ROLE_ARTICLE_MANAGER]


def sync_admin_users_policy(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")
    UserProfile = apps.get_model("kb", "UserProfile")

    groups = {group.name: group for group in Group.objects.filter(name__in=[ROLE_DISABLED_USER, *ROLE_ACCESS_GROUPS])}
    disabled_group = groups.get(ROLE_DISABLED_USER)
    regular_group = groups.get(ROLE_REGULAR_USER)
    admin_group = groups.get(ROLE_ADMIN_USERS)

    for user in User.objects.all():
        profile = UserProfile.objects.filter(user=user).first()
        if profile is None:
            profile = UserProfile.objects.create(
                user=user,
                account_type="user",
                auth_source="local",
                can_access_main_site=True,
                preferred_language="en",
            )

        has_disabled = bool(disabled_group and user.groups.filter(pk=disabled_group.pk).exists())
        has_admin = bool(admin_group and user.groups.filter(pk=admin_group.pk).exists())

        # Preserve existing superuser/staff/admin-profile accounts by moving them
        # into Admin Users before the runtime policy makes Admin Users the source
        # of truth for Django Admin access.
        if not has_disabled and not has_admin and (user.is_superuser or user.is_staff or profile.account_type in {"admin", "ldap_admin"}):
            if admin_group:
                user.groups.add(admin_group)
                has_admin = True

        if has_disabled:
            if groups:
                user.groups.remove(*[group for name, group in groups.items() if name in ROLE_ACCESS_GROUPS])
            if user.is_staff or user.is_superuser:
                user.is_staff = False
                user.is_superuser = False
                user.save(update_fields=["is_staff", "is_superuser"])
            target_type = "ldap_user" if profile.auth_source == "ad" else "user"
            updates = []
            if profile.account_type != target_type:
                profile.account_type = target_type
                updates.append("account_type")
            if profile.can_access_main_site:
                profile.can_access_main_site = False
                updates.append("can_access_main_site")
            if updates:
                updates.append("updated_at")
                profile.save(update_fields=updates)
            continue

        if has_admin:
            # Admin Users already has full access, so remove redundant normal role groups.
            for role_name in NON_ADMIN_ROLE_GROUPS:
                group = groups.get(role_name)
                if group:
                    user.groups.remove(group)
            if not user.is_staff or not user.is_superuser:
                user.is_staff = True
                user.is_superuser = True
                user.save(update_fields=["is_staff", "is_superuser"])
            target_type = "ldap_admin" if profile.auth_source == "ad" else "admin"
            updates = []
            if profile.account_type != target_type:
                profile.account_type = target_type
                updates.append("account_type")
            if not profile.can_access_main_site:
                profile.can_access_main_site = True
                updates.append("can_access_main_site")
            if updates:
                updates.append("updated_at")
                profile.save(update_fields=updates)
            continue

        # Non-admin users must not keep staff/superuser flags.
        if user.is_staff or user.is_superuser:
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=["is_staff", "is_superuser"])
        target_type = "ldap_user" if profile.auth_source == "ad" else "user"
        if profile.account_type != target_type:
            profile.account_type = target_type
            profile.save(update_fields=["account_type", "updated_at"])
        if not user.groups.filter(name__in=ROLE_ACCESS_GROUPS).exists() and regular_group:
            user.groups.add(regular_group)


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0005_remove_redundant_admin_view_permissions"),
    ]

    operations = [
        migrations.RunPython(sync_admin_users_policy, migrations.RunPython.noop),
    ]
