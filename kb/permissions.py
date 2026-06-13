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

ROLE_REGULAR_USER = "Regular User"
ROLE_ARTICLE_WRITER = "Article Writer"
ROLE_ARTICLE_MANAGER = "Article Manager"
ROLE_ADMIN_USERS = "Admin Users"

ROLE_GROUP_NAMES = (
    ROLE_REGULAR_USER,
    ROLE_ARTICLE_WRITER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ADMIN_USERS,
)

ROLE_DEFINITIONS = {
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
    ROLE_REGULAR_USER: 10,
    ROLE_ARTICLE_WRITER: 20,
    ROLE_ARTICLE_MANAGER: 30,
    ROLE_ADMIN_USERS: 40,
}


def permission_codename_to_full(codename: str) -> str:
    return f"kb.{codename}"


def _permission_exists(user, codename: str) -> bool:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return bool(user.has_perm(permission_codename_to_full(codename)))


def user_has_kb_permission(user, codename: str) -> bool:
    """Return True if the user has one of the custom DjOpenKB permissions."""
    return _permission_exists(user, codename)


def user_can_view_articles(user) -> bool:
    """Published articles remain public for anonymous visitors.

    For authenticated users, the explicit view permission allows admins to
    disable article viewing by removing that permission/group.
    """
    if not getattr(user, "is_authenticated", False):
        return True
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


def user_can_use_admin_tools(user) -> bool:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
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
    """Create/update the four standard DjOpenKB role groups."""
    try:
        custom_permissions = create_article_permissions()
    except (DatabaseError, OperationalError, ProgrammingError):
        return

    for role_name, definition in ROLE_DEFINITIONS.items():
        group, _created = Group.objects.get_or_create(name=role_name)
        perms = [custom_permissions[codename] for codename in definition["permissions"]]

        # Give Article Manager enough model permissions to review from Django admin
        # only if is_staff is later enabled manually. The main pending-review UI
        # still uses the custom can_manage_articles permission.
        if role_name == ROLE_ARTICLE_MANAGER:
            perms.extend(_model_permissions("kb", "suggestedarticle", {"view", "change"}))

        if role_name == ROLE_ADMIN_USERS:
            perms.extend(Permission.objects.filter(content_type__app_label="kb"))
            perms.extend(Permission.objects.filter(content_type__app_label="auth", content_type__model__in={"user", "group"}))

        group.permissions.set(sorted(set(perms), key=lambda permission: permission.pk))


def _model_permissions(app_label: str, model: str, actions: set[str]):
    codenames = [f"{action}_{model}" for action in actions]
    return list(Permission.objects.filter(content_type__app_label=app_label, codename__in=codenames))


def assign_single_role_group(user, role_name: str, *, clear_direct_permissions: bool = False):
    """Assign exactly one DjOpenKB role group to a user.

    Existing non-DjOpenKB groups are preserved. Direct user permissions are kept
    by default so admins can use checkbox overrides in the normal Django User
    form. Pass clear_direct_permissions=True when you intentionally want the
    selected role group to be the only effective DjOpenKB permission source.
    """
    if not getattr(user, "pk", None) or role_name not in ROLE_GROUP_NAMES:
        return

    seed_djopenkb_role_groups()
    role_group = Group.objects.get(name=role_name)
    user.groups.remove(*Group.objects.filter(name__in=ROLE_GROUP_NAMES).exclude(pk=role_group.pk))
    user.groups.add(role_group)

    if clear_direct_permissions:
        kb_perms = Permission.objects.filter(content_type__app_label="kb")
        user.user_permissions.remove(*kb_perms)

    sync_user_staff_flags_from_roles(user)


def assign_default_kb_role_group(user):
    """Put a newly-created user into a default DjOpenKB role group.

    Superusers/staff/profile admins become Admin Users. Everyone else becomes
    Regular User, including new AD-created users.
    """
    if not getattr(user, "pk", None):
        return

    try:
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
        has_admin_group = user.groups.filter(name=ROLE_ADMIN_USERS).exists()
        has_article_manager_group = user.groups.filter(name=ROLE_ARTICLE_MANAGER).exists()
    except (DatabaseError, OperationalError, ProgrammingError):
        return

    profile = getattr(user, "kb_profile", None)
    should_be_staff = bool(
        getattr(user, "is_superuser", False)
        or has_admin_group
        or has_article_manager_group
        or (profile and getattr(profile, "is_admin_type", False))
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
