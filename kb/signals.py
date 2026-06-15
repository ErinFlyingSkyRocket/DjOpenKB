from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_migrate, post_save
from django.dispatch import receiver

from .models import SuggestedArticle, UserProfile


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    """Create/sync the main-site profile whenever a User is created.

    New users are placed into a default DjOpenKB role group:
    - staff/superuser/admin-type accounts -> Admin Users
    - all other local/AD accounts -> Regular User (view-only)

    The article author snapshot is also refreshed while the user still exists,
    so username/email changes are reflected in the article metadata. If the
    user is later deleted, the last stored snapshot remains on the article.
    """
    if created:
        if instance.is_superuser or instance.is_staff:
            account_type = UserProfile.AccountType.ADMIN
        else:
            account_type = UserProfile.AccountType.USER

        UserProfile.objects.get_or_create(
            user=instance,
            defaults={
                "account_type": account_type,
                "auth_source": UserProfile.AuthSource.LOCAL,
                "can_access_main_site": True,
                "preferred_language": "en",
            },
        )

    try:
        from .permissions import assign_default_kb_role_group

        assign_default_kb_role_group(instance)
    except Exception:
        # Do not break migrations or login if auth_group/auth_permission are not
        # ready yet during initial deployment.
        pass

    # Keep author snapshot details updated when a user edits their name/email,
    # and avoid crashing during login if the model code and signal code are ever
    # temporarily out of sync. Login updates User.last_login, which also fires
    # this signal.
    for article in SuggestedArticle.objects.filter(owner=instance):
        if not hasattr(article, "refresh_author_snapshot"):
            continue

        article.refresh_author_snapshot()
        SuggestedArticle.objects.filter(pk=article.pk).update(
            author_username_snapshot=article.author_username_snapshot,
            author_name_snapshot=article.author_name_snapshot,
            author_email_snapshot=article.author_email_snapshot,
            author_account_type_snapshot=article.author_account_type_snapshot,
        )


@receiver(m2m_changed, sender=get_user_model().groups.through)
def sync_user_role_flags(sender, instance, action, reverse=False, pk_set=None, **kwargs):
    """Update staff/default-role state when DjOpenKB group membership changes.

    The signal can be fired from the User side (user.groups.add/remove) or from
    the Group side (group.user_set.add/remove). Handle both so Admin Users group
    membership keeps Django staff access in sync no matter where it is edited.
    """
    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    try:
        from django.contrib.auth.models import Group

        from .permissions import assign_default_kb_role_group, sync_user_staff_flags_from_roles

        UserModel = get_user_model()
        users = []

        if isinstance(instance, UserModel):
            users = [instance]
        elif isinstance(instance, Group) and pk_set:
            users = list(UserModel.objects.filter(pk__in=pk_set))

        for user in users:
            if not getattr(user, "_djopenkb_syncing_role_groups", False):
                assign_default_kb_role_group(user)
            sync_user_staff_flags_from_roles(user)
    except Exception:
        pass


@receiver(post_migrate)
def seed_role_groups_after_migrate(sender, app_config=None, **kwargs):
    """Refresh role groups after migrations create/update auth permissions."""
    if app_config is not None and getattr(app_config, "name", "") != "kb":
        return

    try:
        from .permissions import seed_djopenkb_role_groups

        seed_djopenkb_role_groups()
    except Exception:
        pass
