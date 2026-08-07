from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import m2m_changed, post_migrate, post_save, pre_delete
from django.dispatch import receiver

from .models import SuggestedArticle, UserProfile


@receiver(pre_delete, sender=SuggestedArticle)
def purge_deleted_article_edit_checkpoints(sender, instance, **kwargs):
    """Remove uncommitted edit-checkpoint files when an article is truly deleted."""
    from .models import ArticleEditWorkspace
    from .views.services import _delete_article_edit_workspace_files

    workspaces = list(
        ArticleEditWorkspace.objects.filter(article_id=instance.pk).select_related("owner")
    )
    for workspace in workspaces:
        transaction.on_commit(
            lambda checkpoint=workspace: _delete_article_edit_workspace_files(
                checkpoint,
                actor=None,
                keep_filenames=[],
            )
        )


@receiver(pre_delete, sender=get_user_model())
def purge_deleted_user_checkpoint(sender, instance, **kwargs):
    """Purge private New Article and existing-article checkpoints on permanent account deletion.

    Setting ``is_active=False`` or assigning the Disabled User role does not
    delete the User row and therefore does not run this cleanup. Published
    knowledge remains as orphaned content; unpublished saved articles and
    private update copies are removed with the deleted account.
    """
    from .account_cleanup import prepare_user_account_deletion

    prepare_user_account_deletion(instance)


def _refresh_owned_article_author_snapshots(user):
    """Refresh all owned article author snapshots with one database UPDATE."""

    try:
        profile = user.kb_profile
    except UserProfile.DoesNotExist:
        profile = None

    if profile is not None:
        account_type = profile.get_account_type_display()
    elif user.is_superuser or user.is_staff:
        account_type = "Admin"
    else:
        account_type = ""

    SuggestedArticle.objects.filter(owner_id=user.pk).update(
        author_username_snapshot=user.get_username(),
        author_name_snapshot=user.get_full_name().strip(),
        author_email_snapshot=user.email or "",
        author_account_type_snapshot=account_type,
    )


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    """Create/sync the main-site profile and author identity snapshots.

    Routine saves such as Django's ``last_login`` update must not scan/update
    every article owned by the user. Role normalisation runs only when creation
    or staff/superuser state may have changed, while article snapshots refresh
    only when identity/account-type fields may have changed.
    """
    update_fields = kwargs.get("update_fields")
    changed_fields = set(update_fields or [])

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

    role_fields = {"is_staff", "is_superuser"}
    should_normalise_role = bool(
        created
        or update_fields is None
        or changed_fields.intersection(role_fields)
    )
    if should_normalise_role:
        try:
            from .permissions import assign_default_kb_role_group

            assign_default_kb_role_group(instance)
        except Exception:
            # Do not break migrations if auth_group/auth_permission are not ready
            # during initial deployment. Runtime role changes are also normalised
            # by the dedicated m2m role signal below.
            pass

    snapshot_fields = {
        "username",
        "first_name",
        "last_name",
        "email",
        "is_staff",
        "is_superuser",
    }
    should_refresh_snapshots = bool(
        created
        or update_fields is None
        or changed_fields.intersection(snapshot_fields)
    )
    if should_refresh_snapshots:
        _refresh_owned_article_author_snapshots(instance)


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
            enforce_manager_role_precedence,
            enforce_regular_user_default_only,
            sync_user_staff_flags_from_roles,
        )

        UserModel = get_user_model()
        user_ids = []

        if isinstance(instance, UserModel):
            # Normalisation itself can add/remove groups. Do not schedule a new
            # normalisation callback for those internal group mutations, or an
            # on_commit callback can recursively invoke itself in autocommit
            # mode and eventually raise RecursionError.
            if (
                getattr(instance, "_djopenkb_syncing_role_groups", False)
                or getattr(instance, "_djopenkb_normalising_role_groups", False)
            ):
                return
            user_ids = [instance.pk]
        elif isinstance(instance, Group) and pk_set:
            user_ids = list(pk_set)

        def normalise_user_roles(user_pk):
            user = UserModel.objects.filter(pk=user_pk).first()
            if (
                user is None
                or getattr(user, "_djopenkb_syncing_role_groups", False)
                or getattr(user, "_djopenkb_normalising_role_groups", False)
            ):
                return

            user._djopenkb_normalising_role_groups = True
            try:
                if enforce_disabled_user_exclusive(user):
                    return
                enforce_admin_users_exclusive(user)
                enforce_manager_role_precedence(user)
                enforce_regular_user_default_only(user)
                assign_default_kb_role_group(user)
                sync_user_staff_flags_from_roles(user)
            finally:
                user._djopenkb_normalising_role_groups = False

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

