"""Knowledge Repository role and permission helpers.

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
PERM_DELETE_ARTICLES = "can_delete_articles"
PERM_VIEW_INTERNAL_ARTICLES = "can_view_internal_articles"
PERM_ADD_INTERNAL_ARTICLES = "can_add_internal_articles"
PERM_MANAGE_INTERNAL_ARTICLES = "can_manage_internal_articles"
PERM_DELETE_INTERNAL_ARTICLES = "can_delete_internal_articles"
PERM_USE_ADMIN_TOOLS = "can_use_admin_tools"

PERMISSION_LABELS = {
    PERM_VIEW_ARTICLES: "Can view published public articles",
    PERM_ADD_ARTICLES: "Can add/submit public articles for approval",
    PERM_MANAGE_ARTICLES: "Can approve/manage pending public article reviews",
    PERM_DELETE_ARTICLES: "Can delete public articles",
    PERM_VIEW_INTERNAL_ARTICLES: "Can view internal articles",
    PERM_ADD_INTERNAL_ARTICLES: "Can add/submit internal articles for approval",
    PERM_MANAGE_INTERNAL_ARTICLES: "Can approve/manage pending internal article reviews",
    PERM_DELETE_INTERNAL_ARTICLES: "Can delete internal articles",
    PERM_USE_ADMIN_TOOLS: "Can use Knowledge Repository admin tools",
}

ROLE_DISABLED_USER = "Disabled User"
ROLE_REGULAR_USER = "Regular User"
ROLE_ARTICLE_WRITER = "Article Writer"
ROLE_ARTICLE_APPROVER = "Article Approver"
ROLE_ARTICLE_MANAGER = "Article Manager"
ROLE_INTERNAL_USER = "Internal User"
ROLE_INTERNAL_ARTICLE_WRITER = "Internal Article Writer"
ROLE_INTERNAL_ARTICLE_APPROVER = "Internal Article Approver"
ROLE_INTERNAL_ARTICLE_MANAGER = "Internal Article Manager"
ROLE_ADMIN_USERS = "Admin Users"

ROLE_GROUP_NAMES = (
    ROLE_DISABLED_USER,
    ROLE_REGULAR_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_INTERNAL_USER,
    ROLE_INTERNAL_ARTICLE_WRITER,
    ROLE_INTERNAL_ARTICLE_APPROVER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)

# Permission-bearing access roles. A user may belong to more than one of these
# groups if needed, because Django permissions are additive. Disabled User is
# intentionally kept separate and always overrides these access roles.
ROLE_ACCESS_GROUP_NAMES = (
    ROLE_REGULAR_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_INTERNAL_USER,
    ROLE_INTERNAL_ARTICLE_WRITER,
    ROLE_INTERNAL_ARTICLE_APPROVER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)

ROLE_ELEVATED_GROUP_NAMES = (
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_INTERNAL_ARTICLE_WRITER,
    ROLE_INTERNAL_ARTICLE_APPROVER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)

ROLE_INTERNAL_GROUP_NAMES = (
    ROLE_INTERNAL_USER,
    ROLE_INTERNAL_ARTICLE_WRITER,
    ROLE_INTERNAL_ARTICLE_APPROVER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
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
            "Fallback viewer account. Can view published articles after sign-in, but cannot create, edit, review, or use admin tools. Automatically removed when Article Writer, Article Approver, or Article Manager is assigned."
        ),
        "permissions": (PERM_VIEW_ARTICLES,),
    },
    ROLE_ARTICLE_WRITER: {
        "description": _(
            "Article contributor. Can view articles, create articles, save drafts, submit for approval, manage their own article drafts/updates, and request deletion of their own published articles. Regular User is not required."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_ADD_ARTICLES),
    },
    ROLE_ARTICLE_APPROVER: {
        "description": _(
            "Article approver. Can view articles, review pending public articles/updates and public deletion requests, edit content while articles/updates remain pending, keep them pending after review edits, and approve or reject them; cannot create, directly delete, or edit already-published articles by default. Regular User is not required."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_MANAGE_ARTICLES),
    },
    ROLE_ARTICLE_MANAGER: {
        "description": _(
            "Article manager. Can view public articles, create public articles, edit/manage public articles, review public pending articles/updates/deletion requests, approve/reject submissions, and delete public articles in scope. Regular User is not required."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_ADD_ARTICLES, PERM_MANAGE_ARTICLES, PERM_DELETE_ARTICLES),
    },
    ROLE_INTERNAL_USER: {
        "description": _(
            "Internal viewer add-on. Can view public articles and internal articles, but cannot create or review internal articles."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_VIEW_INTERNAL_ARTICLES),
    },
    ROLE_INTERNAL_ARTICLE_WRITER: {
        "description": _(
            "Internal writer add-on. Can view public/internal articles, create or maintain their own internal article drafts/submissions, and request deletion of their own published internal articles."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_VIEW_INTERNAL_ARTICLES, PERM_ADD_INTERNAL_ARTICLES),
    },
    ROLE_INTERNAL_ARTICLE_APPROVER: {
        "description": _(
            "Internal approver add-on. Can view public/internal articles, edit content while internal articles/updates remain pending, keep them pending after review edits, and approve or reject internal pending articles/updates/deletion requests. Cannot directly delete or edit already-published internal articles by default."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_VIEW_INTERNAL_ARTICLES, PERM_MANAGE_INTERNAL_ARTICLES),
    },
    ROLE_INTERNAL_ARTICLE_MANAGER: {
        "description": _(
            "Internal manager add-on. Can view public/internal articles, create, edit/manage, review, approve/reject internal submissions/deletion requests, and delete internal articles in scope."
        ),
        "permissions": (PERM_VIEW_ARTICLES, PERM_VIEW_INTERNAL_ARTICLES, PERM_ADD_INTERNAL_ARTICLES, PERM_MANAGE_INTERNAL_ARTICLES, PERM_DELETE_INTERNAL_ARTICLES),
    },
    ROLE_ADMIN_USERS: {
        "description": _(
            "Knowledge Repository administrator. Can create/publish articles directly and is synchronised to Django staff/superuser access for full Django Admin management."
        ),
        "permissions": (
            PERM_VIEW_ARTICLES,
            PERM_ADD_ARTICLES,
            PERM_MANAGE_ARTICLES,
            PERM_DELETE_ARTICLES,
            PERM_VIEW_INTERNAL_ARTICLES,
            PERM_ADD_INTERNAL_ARTICLES,
            PERM_MANAGE_INTERNAL_ARTICLES,
            PERM_DELETE_INTERNAL_ARTICLES,
            PERM_USE_ADMIN_TOOLS,
        ),
    },
}

ROLE_PRIORITY = {
    ROLE_DISABLED_USER: 100,
    ROLE_REGULAR_USER: 10,
    ROLE_ARTICLE_WRITER: 20,
    ROLE_ARTICLE_APPROVER: 30,
    ROLE_ARTICLE_MANAGER: 40,
    ROLE_INTERNAL_USER: 22,
    ROLE_INTERNAL_ARTICLE_WRITER: 32,
    ROLE_INTERNAL_ARTICLE_APPROVER: 42,
    ROLE_INTERNAL_ARTICLE_MANAGER: 52,
    ROLE_ADMIN_USERS: 60,
}


def permission_codename_to_full(codename: str) -> str:
    return f"kb.{codename}"


def user_has_disabled_role(user) -> bool:
    """Return True when the user is assigned to the Disabled User role.

    Disabled User is a deliberate no-access role. It overrides direct and
    group-based Knowledge Repository permissions while still keeping the account record for
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
    """Return True if the user has one of the custom Knowledge Repository permissions."""
    return _permission_exists(user, codename)


def user_can_view_articles(user) -> bool:
    """Return True for signed-in users allowed to view public/general articles.

    Knowledge Repository is login-only, so anonymous visitors must not be able to
    view published articles or their uploaded images.
    Internal article access is handled separately by user_can_view_internal_articles().
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if user_has_disabled_role(user):
        return False
    return bool(
        user_has_kb_permission(user, PERM_VIEW_ARTICLES)
        or user_has_kb_permission(user, PERM_VIEW_INTERNAL_ARTICLES)
        or user_can_add_articles(user)
        or user_can_manage_articles(user)
        or user_can_add_internal_articles(user)
        or user_can_manage_internal_articles(user)
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


def user_can_delete_articles(user) -> bool:
    return bool(
        user_has_kb_permission(user, PERM_DELETE_ARTICLES)
        or user_can_use_admin_tools(user)
    )


def user_can_view_internal_articles(user) -> bool:
    return bool(
        user_has_kb_permission(user, PERM_VIEW_INTERNAL_ARTICLES)
        or user_can_add_internal_articles(user)
        or user_can_manage_internal_articles(user)
        or user_can_use_admin_tools(user)
    )


def user_can_add_internal_articles(user) -> bool:
    return bool(
        user_has_kb_permission(user, PERM_ADD_INTERNAL_ARTICLES)
        or user_can_use_admin_tools(user)
    )


def user_can_manage_internal_articles(user) -> bool:
    return bool(
        user_has_kb_permission(user, PERM_MANAGE_INTERNAL_ARTICLES)
        or user_can_use_admin_tools(user)
    )


def user_can_delete_internal_articles(user) -> bool:
    return bool(
        user_has_kb_permission(user, PERM_DELETE_INTERNAL_ARTICLES)
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
    """Only Article Manager and Admin Users accounts can see dislike counts.

    Article Approver can review pending articles, but dislike totals are kept
    private from approvers and normal users. Disabled User always loses access,
    even if the account still has older direct permissions or stale groups.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if user_has_disabled_role(user):
        return False
    if user_can_use_admin_tools(user):
        return True
    try:
        return user.groups.filter(name__in=(ROLE_ARTICLE_MANAGER, ROLE_INTERNAL_ARTICLE_MANAGER)).exists()
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def user_can_use_admin_tools(user) -> bool:
    """Return True only for full administrators.

    Admin Users are synchronised to Django superuser status. Direct article
    permission exceptions no longer grant Django Admin/admin-tool access.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if user_has_disabled_role(user):
        return False
    return bool(getattr(user, "is_superuser", False))


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
    return ", ".join(labels) if labels else str(_("No Knowledge Repository role permissions"))




def role_group_summary(user) -> str:
    """Return all active Knowledge Repository role groups for display.

    Internal roles are add-ons, so showing only the highest-priority role can be
    misleading. This display helper keeps the authorization logic unchanged but
    makes admin/profile screens clearer.
    """
    if not getattr(user, "is_authenticated", False):
        return ""
    role_names = user_role_group_names(user)
    if user_has_disabled_role(user):
        return ROLE_DISABLED_USER
    if role_names:
        return ", ".join(role_names)
    if getattr(user, "is_superuser", False):
        return ROLE_ADMIN_USERS
    return ROLE_REGULAR_USER


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
        _("Knowledge Repository role guide"),
        format_html_join(
            "",
            "<li><strong>{}</strong>: {}</li>",
            ((name, definition["description"]) for name, definition in ROLE_DEFINITIONS.items()),
        ),
        _(
            "Regular User is the fallback public viewer role. Internal roles are add-on roles that also include public article viewing. "
            "Direct user permissions can still be ticked for custom combinations. Django evaluates group permissions and direct user permissions together."
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
    """Create/update the standard Knowledge Repository role groups."""
    try:
        custom_permissions = create_article_permissions()
    except (DatabaseError, OperationalError, ProgrammingError):
        return

    for role_name, definition in ROLE_DEFINITIONS.items():
        group, _created = Group.objects.get_or_create(name=role_name)
        perms = [custom_permissions[codename] for codename in definition["permissions"]]

        # Admin Users do not need explicit Django model view permissions here.
        # They are synchronised to superuser status, and superuser status grants
        # full Django Admin access. Keeping the group to custom Knowledge
        # Repository permissions avoids confusing/redundant "view only" grants.
        group.permissions.set(sorted(set(perms), key=lambda permission: permission.pk))


def set_user_direct_kb_permission(user, codename: str, enabled: bool):
    """Add/remove one custom Knowledge Repository permission directly on a user."""
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


def _profile_user_type_for_auth_source(profile):
    """Return the non-admin profile type matching the profile auth source."""
    from .models import UserProfile

    if getattr(profile, "auth_source", None) == UserProfile.AuthSource.AD:
        return UserProfile.AccountType.LDAP_USER
    return UserProfile.AccountType.USER


def _profile_admin_type_for_auth_source(profile):
    """Return the admin profile type matching the profile auth source."""
    from .models import UserProfile

    if getattr(profile, "auth_source", None) == UserProfile.AuthSource.AD:
        return UserProfile.AccountType.LDAP_ADMIN
    return UserProfile.AccountType.ADMIN


def _save_profile_without_role_side_effects(profile, *, update_fields: list[str]):
    """Save UserProfile fields without triggering role sync loops."""
    if not update_fields:
        return
    profile._djopenkb_syncing_from_roles = True
    try:
        profile.save(update_fields=update_fields)
    finally:
        profile._djopenkb_syncing_from_roles = False


def sync_user_profile_type_from_roles(user):
    """Keep the profile label in sync with the effective role state.

    The Admin Users group is now the source of truth for full Django Admin
    access. When a local account is placed in Admin Users it becomes a local
    admin; when an AD-managed account is placed in Admin Users it becomes an
    LDAP admin. Removing Admin Users demotes the profile back to local user or
    LDAP user according to its authentication source.
    """
    if not getattr(user, "pk", None):
        return

    try:
        from .models import UserProfile

        profile, _created = UserProfile.objects.get_or_create(user=user)
        has_disabled_role = user.groups.filter(name=ROLE_DISABLED_USER).exists()
        has_admin_group = user.groups.filter(name=ROLE_ADMIN_USERS).exists()

        update_fields = []
        if has_disabled_role:
            target_type = _profile_user_type_for_auth_source(profile)
        elif has_admin_group:
            target_type = _profile_admin_type_for_auth_source(profile)
        else:
            target_type = _profile_user_type_for_auth_source(profile)

        if profile.account_type != target_type:
            profile.account_type = target_type
            update_fields.append("account_type")

        # ``can_access_main_site`` is kept as a legacy compatibility field only.
        # The built-in User.is_active flag controls whether an account can sign in,
        # while the Disabled User group controls the clean disabled-account page.
        # Keep the legacy flag enabled so it cannot accidentally block the newer flow.
        if not profile.can_access_main_site:
            profile.can_access_main_site = True
            update_fields.append("can_access_main_site")

        if update_fields:
            update_fields.append("updated_at")
            _save_profile_without_role_side_effects(profile, update_fields=update_fields)
    except (DatabaseError, OperationalError, ProgrammingError):
        return


def enforce_regular_user_default_only(user):
    """Keep Regular User as the fallback role, not a redundant extra role.

    A user may still combine elevated standard roles such as Article Writer and
    Article Approver/Manager, and may also keep custom non-role groups for
    future features such as notifications. Regular User is automatically removed
    once a higher Knowledge Repository role is present. It is added back only
    when the account has no standard Knowledge Repository role at all.
    """
    if not getattr(user, "pk", None):
        return False

    try:
        if user.groups.filter(name=ROLE_DISABLED_USER).exists():
            return False
        if not user.groups.filter(name=ROLE_REGULAR_USER).exists():
            return False
        if not user.groups.filter(name__in=ROLE_ELEVATED_GROUP_NAMES).exists():
            return False

        previous_syncing = getattr(user, "_djopenkb_syncing_role_groups", False)
        user._djopenkb_syncing_role_groups = True
        try:
            user.groups.remove(*Group.objects.filter(name=ROLE_REGULAR_USER))
        finally:
            user._djopenkb_syncing_role_groups = previous_syncing
        return True
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def enforce_admin_users_exclusive(user):
    """Make Admin Users exclusive among Knowledge Repository role groups.

    Normal users may still belong to multiple non-admin/custom groups, but once
    Admin Users is assigned it grants full access, so Regular User / Article
    Writer / Article Approver / Article Manager and direct Knowledge Repository
    permission add-ons become redundant and are removed.
    """
    if not getattr(user, "pk", None):
        return False

    try:
        if user.groups.filter(name=ROLE_DISABLED_USER).exists():
            return False
        if not user.groups.filter(name=ROLE_ADMIN_USERS).exists():
            return False

        seed_djopenkb_role_groups()
        previous_syncing = getattr(user, "_djopenkb_syncing_role_groups", False)
        user._djopenkb_syncing_role_groups = True
        try:
            user.groups.remove(
                *Group.objects.filter(
                    name__in=(ROLE_REGULAR_USER, ROLE_ARTICLE_WRITER, ROLE_ARTICLE_APPROVER, ROLE_ARTICLE_MANAGER, ROLE_INTERNAL_USER, ROLE_INTERNAL_ARTICLE_WRITER, ROLE_INTERNAL_ARTICLE_APPROVER, ROLE_INTERNAL_ARTICLE_MANAGER)
                )
            )
        finally:
            user._djopenkb_syncing_role_groups = previous_syncing

        kb_perms = Permission.objects.filter(content_type__app_label="kb")
        user.user_permissions.remove(*kb_perms)
        sync_user_profile_type_from_roles(user)
        return True
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def enforce_disabled_user_exclusive(user, *, clear_sessions: bool = False):
    """Make Disabled User the highest-precedence no-access role.

    Non-disabled users may belong to multiple normal/custom groups for future
    combinations such as article permissions plus notification groups. When the
    Disabled User role is assigned, it wins over every other Knowledge Repository
    access role, removes direct Knowledge Repository permission overrides, and
    immediately clears Django Admin flags so even an admin account becomes non-
    staff/non-superuser.

    Existing browser sessions are intentionally not deleted here. Keeping the
    session until the next request lets DisabledUserLogoutMiddleware identify the
    account and send the user to the clean /account-disabled/ page instead of
    causing a confusing anonymous 404/redirect flow.
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
            # Disabled User must be exclusive against the normal access roles,
            # including Admin Users. Custom non-role groups are left untouched.
            user.groups.remove(*Group.objects.filter(name__in=ROLE_ACCESS_GROUP_NAMES))
        finally:
            user._djopenkb_syncing_role_groups = previous_syncing

        kb_perms = Permission.objects.filter(content_type__app_label="kb")
        user.user_permissions.remove(*kb_perms)

        update_fields = []
        if user.is_staff:
            user.is_staff = False
            update_fields.append("is_staff")
        if user.is_superuser:
            user.is_superuser = False
            update_fields.append("is_superuser")
        if update_fields:
            user.save(update_fields=update_fields)

        sync_user_profile_type_from_roles(user)

        # Do not clear sessions here. DisabledUserLogoutMiddleware handles the
        # next request and redirects the affected browser to /account-disabled/.
        # The clear_sessions parameter is kept for backwards-compatible callers.
        return True
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def assign_single_role_group(user, role_name: str, *, clear_direct_permissions: bool = False):
    """Assign one standard Knowledge Repository role group while preserving custom groups.

    This helper is still used for default role setup and explicit admin actions.
    It does not prevent admins from later adding multiple non-disabled groups.
    Disabled User and Admin Users are exclusive override roles. Regular/writer/
    manager assignments remove those override roles but still allow multiple
    non-admin groups to be added manually later.
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
        elif role_name == ROLE_ADMIN_USERS:
            user.groups.remove(
                *Group.objects.filter(
                    name__in=(ROLE_DISABLED_USER, ROLE_REGULAR_USER, ROLE_ARTICLE_WRITER, ROLE_ARTICLE_APPROVER, ROLE_ARTICLE_MANAGER, ROLE_INTERNAL_USER, ROLE_INTERNAL_ARTICLE_WRITER, ROLE_INTERNAL_ARTICLE_APPROVER, ROLE_INTERNAL_ARTICLE_MANAGER)
                )
            )
        else:
            groups_to_remove = [ROLE_DISABLED_USER, ROLE_ADMIN_USERS]
            if role_name in {ROLE_ARTICLE_WRITER, ROLE_ARTICLE_APPROVER, ROLE_ARTICLE_MANAGER}:
                groups_to_remove.append(ROLE_REGULAR_USER)
            user.groups.remove(*Group.objects.filter(name__in=groups_to_remove))
        user.groups.add(role_group)
    finally:
        user._djopenkb_syncing_role_groups = previous_syncing

    if role_name in {ROLE_DISABLED_USER, ROLE_ADMIN_USERS}:
        clear_direct_permissions = True

    if clear_direct_permissions:
        kb_perms = Permission.objects.filter(content_type__app_label="kb")
        user.user_permissions.remove(*kb_perms)

    try:
        from .models import UserProfile

        profile, _created = UserProfile.objects.get_or_create(user=user)
        if not profile.can_access_main_site:
            profile.can_access_main_site = True
            _save_profile_without_role_side_effects(profile, update_fields=["can_access_main_site", "updated_at"])
    except (DatabaseError, OperationalError, ProgrammingError):
        pass

    if role_name == ROLE_DISABLED_USER:
        enforce_disabled_user_exclusive(user)
    elif role_name == ROLE_ADMIN_USERS:
        enforce_admin_users_exclusive(user)
        sync_user_staff_flags_from_roles(user)
    else:
        sync_user_staff_flags_from_roles(user)


def assign_default_kb_role_group(user):
    """Put a newly-created user into a default Knowledge Repository role group.

    New createsuperuser/staff accounts are normalised into Admin Users only
    when they do not already have a Knowledge Repository role group. Existing
    non-admin role groups are respected so removing Admin Users and assigning
    Regular User/Writer/Manager demotes the account cleanly.
    """
    if not getattr(user, "pk", None):
        return

    try:
        if enforce_disabled_user_exclusive(user):
            return
        if enforce_admin_users_exclusive(user):
            sync_user_staff_flags_from_roles(user)
            return

        enforce_regular_user_default_only(user)

        existing_role_groups = user.groups.filter(name__in=ROLE_GROUP_NAMES)
        if existing_role_groups.exists():
            sync_user_staff_flags_from_roles(user)
            return

        if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
            role_name = ROLE_ADMIN_USERS
        else:
            role_name = ROLE_REGULAR_USER

        assign_single_role_group(user, role_name)
    except (DatabaseError, OperationalError, ProgrammingError):
        # During early migrations the auth/group tables may not exist yet.
        return


def sync_user_staff_flags_from_roles(user):
    """Keep Django admin flags aligned with Knowledge Repository role groups.

    Current policy:
    - Disabled User is a hard override and removes Django Admin access.
    - Admin Users is the source of truth for full staff/superuser access.
    - Removing Admin Users and assigning a non-admin role removes staff/superuser.
    """
    if not getattr(user, "pk", None):
        return

    try:
        has_disabled_role = user.groups.filter(name=ROLE_DISABLED_USER).exists()
        has_admin_group = user.groups.filter(name=ROLE_ADMIN_USERS).exists()
    except (DatabaseError, OperationalError, ProgrammingError):
        return

    if has_disabled_role:
        should_be_superuser = False
    else:
        should_be_superuser = bool(has_admin_group)
    should_be_staff = should_be_superuser

    update_fields = []
    if user.is_staff != should_be_staff:
        user.is_staff = should_be_staff
        update_fields.append("is_staff")

    if user.is_superuser != should_be_superuser:
        user.is_superuser = should_be_superuser
        update_fields.append("is_superuser")

    if update_fields:
        user.save(update_fields=update_fields)

    sync_user_profile_type_from_roles(user)

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


def require_view_internal_articles(user):
    if not user_can_view_internal_articles(user):
        raise PermissionDenied(_("You do not have permission to view internal articles."))
    return True


def require_add_internal_articles(user):
    if not user_can_add_internal_articles(user):
        raise PermissionDenied(_("You do not have permission to add internal articles."))
    return True


def require_manage_internal_articles(user):
    if not user_can_manage_internal_articles(user):
        raise PermissionDenied(_("You do not have permission to manage internal articles."))
    return True


def require_admin_tools(user):
    if not user_can_use_admin_tools(user):
        raise PermissionDenied(_("You do not have permission to use admin tools."))
    return True
