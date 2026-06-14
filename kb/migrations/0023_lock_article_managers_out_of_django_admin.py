from django.db import migrations


def lock_article_managers_out_of_django_admin(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("kb", "UserProfile")

    article_manager_group = Group.objects.filter(name="Article Manager").first()
    admin_group = Group.objects.filter(name="Admin Users").first()

    if article_manager_group:
        article_manager_permissions = Permission.objects.filter(
            content_type__app_label="kb",
            codename__in=["can_view_articles", "can_manage_articles"],
        )
        article_manager_group.permissions.set(article_manager_permissions)

    direct_admin_permission = Permission.objects.filter(
        content_type__app_label="kb",
        codename="can_use_admin_tools",
    ).first()

    admin_profile_types = {"admin", "ldap_admin"}
    users = User.objects.all().prefetch_related("groups", "user_permissions")
    for user in users:
        if user.is_superuser:
            continue

        groups = set(user.groups.values_list("name", flat=True))
        has_admin_group = admin_group is not None and "Admin Users" in groups
        has_direct_admin_permission = bool(
            direct_admin_permission
            and user.user_permissions.filter(pk=direct_admin_permission.pk).exists()
        )
        profile_is_admin = UserProfile.objects.filter(
            user=user,
            account_type__in=admin_profile_types,
        ).exists()

        should_be_staff = bool(has_admin_group or has_direct_admin_permission or profile_is_admin)
        if user.is_staff != should_be_staff:
            user.is_staff = should_be_staff
            user.save(update_fields=["is_staff"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0022_refine_role_permissions_vote_and_log_access"),
    ]

    operations = [
        migrations.RunPython(lock_article_managers_out_of_django_admin, noop_reverse),
    ]
