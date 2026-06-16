from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import m2m_changed, post_migrate, post_save
from django.dispatch import receiver

from .models import SuggestedArticle, UserProfile


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    """Create/sync the main-site profile whenever a User is created.

    New users are placed into a default Knowledge Repository role group:
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
    """Update staff/default-role state when Knowledge Repository group membership changes.

    The signal can be fired from the User side (user.groups.add/remove) or from
    the Group side (group.user_set.add/remove). Handle both so role membership
    stays normalised after the full transaction completes. This avoids the admin
    form temporarily clearing groups and accidentally re-adding Regular User
    before the selected Writer/Approver/Manager role is saved.
    """
    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    try:
        from django.contrib.auth.models import Group

        from .permissions import (
            assign_default_kb_role_group,
            enforce_admin_users_exclusive,
            enforce_disabled_user_exclusive,
            enforce_regular_user_default_only,
            sync_user_staff_flags_from_roles,
        )

        UserModel = get_user_model()
        user_ids = []

        if isinstance(instance, UserModel):
            user_ids = [instance.pk]
        elif isinstance(instance, Group) and pk_set:
            user_ids = list(pk_set)

        def normalise_user_roles(user_pk):
            user = UserModel.objects.filter(pk=user_pk).first()
            if user is None or getattr(user, "_djopenkb_syncing_role_groups", False):
                return
            if enforce_disabled_user_exclusive(user):
                return
            enforce_admin_users_exclusive(user)
            enforce_regular_user_default_only(user)
            assign_default_kb_role_group(user)
            sync_user_staff_flags_from_roles(user)

        for user_id in user_ids:
            transaction.on_commit(lambda pk=user_id: normalise_user_roles(pk))
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

@receiver(post_save, sender=LogEntry)
def mirror_django_admin_logentry(sender, instance, created, **kwargs):
    """Copy Django Admin add/change/delete LogEntry rows into AdminActivityLog.

    Django writes LogEntry only after admin object actions succeed. Keeping a
    separate append-only log lets Knowledge Repository apply the same retention
    and immutability controls as the other audit tables.
    """
    if not created:
        return

    try:
        from .admin_audit import log_admin_logentry

        log_admin_logentry(instance)
    except Exception:
        pass

