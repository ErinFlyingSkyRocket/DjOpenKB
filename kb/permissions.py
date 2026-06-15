"""DjOpenKB role and permission helpers.

Django's built-in permissions are additive: a user gets permissions from all
assigned groups plus any direct user permissions. These helpers define the
project's main role groups and provide one place for view/admin checks to use.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, OperationalError, ProgrammingError
from django.utils.translation import gettext_lazy as _


PERM_VIEW_ARTICLES = "can_view_articles"
PERM_ADD_ARTICLES = "can_add_articles"
PERM_MANAGE_ARTICLES = "can_manage_articles"
PERM_USE_ADMIN_TOOLS = "can_use_admin_tools"

PERMISSION_LABELS = {
    PERM_VIEW_ARTICLES: "Can view published articles",
    PERM_ADD_ARTICLES: "Can add/submit articles for approval",
    PERM_MANAGE_ARTICLES: "Can manage pending articles and article reviews",
    PERM_USE_ADMIN_TOOLS: "Can use DjOpenKB admin tools",
}

ROLE_DISABLED_USER = "Disabled User"
ROLE_REGULAR_USER = "Regular User"
ROLE_ARTICLE_WRITER = "Article Writer"
ROLE_ARTICLE_MANAGER = "Article Manager"
ROLE_ADMIN_USERS = "Admin Users"

ROLE_GROUP_NAMES = (
    ROLE_DISABLED_USER,
    ROLE_REGULAR_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)

# Permission-bearing access roles. A user may belong to more than one of these
# groups if needed, because Django permissions are additive. Disabled User is
# intentionally kept separate and always overrides these access roles.
ROLE_ACCESS_GROUP_NAMES = (
    ROLE_REGULAR_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)

ROLE_DEFINITIONS = {
    ROLE_DISABLED_USER: {
        "description": _(
            "Disabled account role. The user account remains in Django for audit/history purposes, but cannot complete login or access the wiki until an admin moves the user to another role."
        ),
        "permissions": (),
    },
    ROLE_REGULAR_USER: {
        "description": _(
            "View-only account. Can view published articles after sign-in, but cannot create, edit, review, or use admin tools."
        ),
        "permissions": (PERM_VIEW_ARTICLES,),
    },
    ROLE_ARTICLE_WRITER: {
        "description": _(
            "Article contributor. Can create articles, save drafts, submit for approval, and manage their own article drafts/updates."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_ADD_ARTICLES),
    },
    ROLE_ARTICLE_MANAGER: {
        "description": _(
            "Article reviewer. Can review, edit, approve, and reject pending articles/updates, but cannot create new articles or use full admin tools by default."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_MANAGE_ARTICLES),
    },
    ROLE_ADMIN_USERS: {
        "description": _(
            "DjOpenKB administrator. Can create/publish articles directly, manage all article reviews, use admin tools, and access Django admin when staff status is enabled."
        ),
        "permissions": (
            PERM_VIEW_ARTICLES,
            PERM_ADD_ARTICLES,
            PERM_MANAGE_ARTICLES,
            PERM_USE_ADMIN_TOOLS,
        ),
    },
}

ROLE_PRIORITY = {
    ROLE_DISABLED_USER: 100,
    ROLE_REGULAR_USER: 10,
    ROLE_ARTICLE_WRITER: 20,
    ROLE_ARTICLE_MANAGER: 30,
    ROLE_ADMIN_USERS: 40,
}


def permission_codename_to_full(codename: str) -> str:
    return f"kb.{codename}"


def user_has_disabled_role(user) -> bool:
    """Return True when the user is assigned to the Disabled User role.

    Disabled User is a deliberate no-access role. It overrides direct and
    group-based DjOpenKB permissions while still keeping the account record for
    audit/history purposes.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return False
    try:
        return user.groups.filter(name=ROLE_DISABLED_USER).exists()
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def _permission_exists(user, codename: str) -> bool:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if user_has_disabled_role(user):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return bool(user.has_perm(permission_codename_to_full(codename)))


def user_has_kb_permission(user, codename: str) -> bool:
    """Return True if the user has one of the custom DjOpenKB permissions."""
    return _permission_exists(user, codename)


def user_can_view_articles(user) -> bool:
    """Return True for signed-in users allowed to view internal articles.

    DjOpenKB is an internal-only wiki, so anonymous visitors must not be able to
    view published articles or their uploaded images.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if user_has_disabled_role(user):
        return False
    return bool(
        user_has_kb_permission(user, PERM_VIEW_ARTICLES)
        or user_can_add_articles(user)
        or user_can_manage_articles(user)
        or user_can_use_admin_tools(user)
    )


def user_can_add_articles(user) -> bool:
    return bool(
        user_has_kb_permission(user, PERM_ADD_ARTICLES)
        or user_can_use_admin_tools(user)
    )


def user_can_manage_articles(user) -> bool:
    return bool(
        user_has_kb_permission(user, PERM_MANAGE_ARTICLES)
        or user_can_use_admin_tools(user)
    )


def user_can_vote_articles(user) -> bool:
    """All active signed-in main-site users may vote on published articles.

    Voting is a baseline feature and is intentionally not split into a
    separate role permission. Article visibility/access is still enforced by
    the article detail view and the main-site access checks.
    """
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and not user_has_disabled_role(user)
        and user_can_view_articles(user)
    )


def user_can_view_dislike_counts(user) -> bool:
    """Only article managers and admins can see down-vote/dislike counts."""
    return bool(user_can_manage_articles(user) or user_can_use_admin_tools(user))


def user_can_use_admin_tools(user) -> bool:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if user_has_disabled_role(user):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if user_has_kb_permission(user, PERM_USE_ADMIN_TOOLS):
        return True
    profile = getattr(user, "kb_profile", None)
    return bool(profile and getattr(profile, "is_admin_type", False))


def user_role_group_names(user) -> list[str]:
    if not getattr(user, "is_authenticated", False):
        return []
    try:
        names = list(user.groups.filter(name__in=ROLE_GROUP_NAMES).values_list("name", flat=True))
    except (DatabaseError, OperationalError, ProgrammingError):
        return []
    return sorted(names, key=lambda name: ROLE_PRIORITY.get(name, 0), reverse=True)


def highest_role_group_name(user) -> str:
    role_names = user_role_group_names(user)
    if user_has_disabled_role(user):
        return ROLE_DISABLED_USER
    if role_names:
        return role_names[0]
    if getattr(user, "is_superuser", False):
        return ROLE_ADMIN_USERS
    profile = getattr(user, "kb_profile", None)
    if profile and getattr(profile, "is_admin_type", False):
        return ROLE_ADMIN_USERS
    return ROLE_REGULAR_USER


def role_permissions_summary(user) -> str:
    if not getattr(user, "is_authenticated", False):
        return ""
    labels = []
    for codename, label in PERMISSION_LABELS.items():
        if user_has_kb_permission(user, codename):
            labels.append(str(_(label)))
    return ", ".join(labels) if labels else str(_("No DjOpenKB role permissions"))


def role_descriptions_text() -> str:
    return "\n".join(
        f"{name}: {definition['description']}"
        for name, definition in ROLE_DEFINITIONS.items()
    )


def role_descriptions_html() -> str:
    from django.utils.html import format_html, format_html_join

    return format_html(
        "<div style='max-width:900px;'>"
        "<p><strong>{}</strong></p>"
        "<ul style='margin-left:18px;'>" 
        "{}"
        "</ul>"
        "<p class='help'>{}</p>"
        "</div>",
        _("DjOpenKB role guide"),
        format_html_join(
            "",
            "<li><strong>{}</strong>: {}</li>",
            ((name, definition["description"]) for name, definition in ROLE_DEFINITIONS.items()),
        ),
        _(
            "Groups provide the standard role template. Direct user permissions can still be ticked for custom combinations. "
            "Django evaluates group permissions and direct user permissions together."
        ),
    )


def create_article_permissions():
    """Create custom SuggestedArticle permissions if they are missing."""
    from .models import SuggestedArticle

    content_type = ContentType.objects.get_for_model(SuggestedArticle)
    created_permissions = {}
    for codename, label in PERMISSION_LABELS.items():
        permission, _created = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": label},
        )
        if permission.name != label:
            permission.name = label
            permission.save(update_fields=["name"])
        created_permissions[codename] = permission
    return created_permissions


def seed_djopenkb_role_groups():
    """Create/update the standard DjOpenKB role groups."""
    try:
        custom_permissions = create_article_permissions()
    except (DatabaseError, OperationalError, ProgrammingError):
        return

    for role_name, definition in ROLE_DEFINITIONS.items():
        group, _created = Group.objects.get_or_create(name=role_name)
        perms = [custom_permissions[codename] for codename in definition["permissions"]]

        # Article Managers review from the normal DjOpenKB pending-review UI only.
        # They must not receive Django Admin model permissions or staff access.
        if role_name == ROLE_ADMIN_USERS:
            perms.extend(_admin_safe_model_permissions())
            perms.extend(Permission.objects.filter(content_type__app_label="auth", content_type__model__in={"user", "group"}))

        group.permissions.set(sorted(set(perms), key=lambda permission: permission.pk))


def _model_permissions(app_label: str, model: str, actions: set[str]):
    codenames = [f"{action}_{model}" for action in actions]
    return list(Permission.objects.filter(content_type__app_label=app_label, codename__in=codenames))


def _admin_safe_model_permissions():
    """Model permissions for Admin Users without destructive log permissions.

    The log admin classes are read-only in the UI, and the standard Admin Users
    group should not be granted add/change/delete permissions on audit tables.
    Retention cleanup commands remain responsible for deleting old logs.
    """
    safe_permissions = []

    safe_permissions.extend(_model_permissions("kb", "suggestedarticle", {"view", "add", "change", "delete"}))
    safe_permissions.extend(_model_permissions("kb", "articlevote", {"view", "change", "delete"}))
    safe_permissions.extend(_model_permissions("kb", "sitesetting", {"view", "change"}))
    safe_permissions.extend(_model_permissions("kb", "userprofile", {"view", "change"}))
    safe_permissions.extend(_model_permissions("kb", "usermfadevice", {"view", "change"}))

    # Audit/log tables are visible to admins but not manually editable/deletable.
    for model_name in ("activitylog", "authactivitylog", "articleimageuploadlog"):
        safe_permissions.extend(_model_permissions("kb", model_name, {"view"}))

    return safe_permissions


def set_user_direct_kb_permission(user, codename: str, enabled: bool):
    """Add/remove one custom DjOpenKB permission directly on a user."""
    if codename not in PERMISSION_LABELS or not getattr(user, "pk", None):
        return

    try:
        permissions = create_article_permissions()
        permission = permissions[codename]
        if enabled:
            user.user_permissions.add(permission)
        else:
            user.user_permissions.remove(permission)
    except (DatabaseError, OperationalError, ProgrammingError):
        return


def user_has_direct_kb_permission(user, codename: str) -> bool:
    if codename not in PERMISSION_LABELS or not getattr(user, "pk", None):
        return False
    try:
        return user.user_permissions.filter(
            content_type__app_label="kb",
            content_type__model="suggestedarticle",
            codename=codename,
        ).exists()
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def enforce_disabled_user_exclusive(user, *, clear_sessions: bool = False):
    """Make Disabled User a hard no-access override without forcing one role normally.

    Non-disabled users may belong to multiple groups for future combinations such
    as article permissions plus notification groups. When the Disabled User role
    is assigned, it must be exclusive among DjOpenKB role groups and direct
    DjOpenKB permissions must be cleared so the user cannot regain access through
    an old override.
    """
    if not getattr(user, "pk", None):
        return False

    try:
        if not user.groups.filter(name=ROLE_DISABLED_USER).exists():
            return False

        seed_djopenkb_role_groups()
        previous_syncing = getattr(user, "_djopenkb_syncing_role_groups", False)
        user._djopenkb_syncing_role_groups = True
        try:
            user.groups.remove(*Group.objects.filter(name__in=ROLE_ACCESS_GROUP_NAMES))
        finally:
            user._djopenkb_syncing_role_groups = previous_syncing

        kb_perms = Permission.objects.filter(content_type__app_label="kb")
        user.user_permissions.remove(*kb_perms)
        sync_user_staff_flags_from_roles(user)

        if clear_sessions:
            try:
                from .mfa import clear_user_auth_sessions

                clear_user_auth_sessions(user)
            except Exception:
                pass

        return True
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def assign_single_role_group(user, role_name: str, *, clear_direct_permissions: bool = False):
    """Assign one standard DjOpenKB role group while preserving custom groups.

    This helper is still used for default role setup and explicit admin actions.
    It does not prevent admins from later adding multiple non-disabled groups.
    If the selected role is Disabled User, it becomes exclusive and clears direct
    DjOpenKB permissions.
    """
    if not getattr(user, "pk", None) or role_name not in ROLE_GROUP_NAMES:
        return

    seed_djopenkb_role_groups()
    role_group = Group.objects.get(name=role_name)

    previous_syncing = getattr(user, "_djopenkb_syncing_role_groups", False)
    user._djopenkb_syncing_role_groups = True
    try:
        if role_name == ROLE_DISABLED_USER:
            user.groups.remove(*Group.objects.filter(name__in=ROLE_ACCESS_GROUP_NAMES))
        else:
            user.groups.remove(*Group.objects.filter(name=ROLE_DISABLED_USER))
        user.groups.add(role_group)
    finally:
        user._djopenkb_syncing_role_groups = previous_syncing

    if role_name == ROLE_DISABLED_USER:
        clear_direct_permissions = True

    if clear_direct_permissions:
        kb_perms = Permission.objects.filter(content_type__app_label="kb")
        user.user_permissions.remove(*kb_perms)

    try:
        from .models import UserProfile

        profile, _created = UserProfile.objects.get_or_create(user=user)
        if role_name != ROLE_DISABLED_USER and not profile.can_access_main_site:
            profile.can_access_main_site = True
            profile.save(update_fields=["can_access_main_site", "updated_at"])
    except (DatabaseError, OperationalError, ProgrammingError):
        pass

    if role_name == ROLE_DISABLED_USER:
        enforce_disabled_user_exclusive(user, clear_sessions=True)
    else:
        sync_user_staff_flags_from_roles(user)

def assign_default_kb_role_group(user):
    """Put a newly-created user into a default DjOpenKB role group.

    Superusers/staff/profile admins become Admin Users. Everyone else becomes
    Regular User, including new AD-created users.
    """
    if not getattr(user, "pk", None):
        return

    try:
        if enforce_disabled_user_exclusive(user):
            return

        existing_role_groups = user.groups.filter(name__in=ROLE_GROUP_NAMES)
        if existing_role_groups.exists():
            sync_user_staff_flags_from_roles(user)
            return

        profile = getattr(user, "kb_profile", None)
        if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False) or (profile and getattr(profile, "is_admin_type", False)):
            role_name = ROLE_ADMIN_USERS
        else:
            role_name = ROLE_REGULAR_USER

        assign_single_role_group(user, role_name)
    except (DatabaseError, OperationalError, ProgrammingError):
        # During early migrations the auth/group tables may not exist yet.
        return


def sync_user_staff_flags_from_roles(user):
    """Keep Django admin staff flag aligned with admin-capable role sources."""
    if not getattr(user, "pk", None):
        return

    try:
        has_disabled_role = user.groups.filter(name=ROLE_DISABLED_USER).exists()
        has_admin_group = user.groups.filter(name=ROLE_ADMIN_USERS).exists()
        has_direct_admin_perm = user_has_direct_kb_permission(user, PERM_USE_ADMIN_TOOLS)
    except (DatabaseError, OperationalError, ProgrammingError):
        return

    profile = getattr(user, "kb_profile", None)
    should_be_staff = bool(
        not has_disabled_role
        and (
            getattr(user, "is_superuser", False)
            or has_admin_group
            or has_direct_admin_perm
            or (profile and getattr(profile, "is_admin_type", False))
        )
    )

    update_fields = []
    if user.is_staff != should_be_staff:
        user.is_staff = should_be_staff
        update_fields.append("is_staff")

    if not should_be_staff and user.is_superuser:
        user.is_superuser = False
        update_fields.append("is_superuser")

    if update_fields:
        user.save(update_fields=update_fields)


def require_view_articles(user):
    if not user_can_view_articles(user):
        raise PermissionDenied(_("You do not have permission to view articles."))
    return True


def require_add_articles(user):
    if not user_can_add_articles(user):
        raise PermissionDenied(_("You do not have permission to add articles."))
    return True


def require_manage_articles(user):
    if not user_can_manage_articles(user):
        raise PermissionDenied(_("You do not have permission to manage articles."))
    return True


def require_admin_tools(user):
    if not user_can_use_admin_tools(user):
        raise PermissionDenied(_("You do not have permission to use admin tools."))
    return True
