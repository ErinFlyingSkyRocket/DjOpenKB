from django import template

from kb.permissions import (
    user_can_add_articles,
    user_can_manage_articles,
    user_can_use_admin_tools,
    user_can_view_articles,
    user_can_view_dislike_counts,
)

register = template.Library()


@register.filter
def is_site_admin(user):
    """Return True when a user should see full DjOpenKB admin-tool navigation."""
    return user_can_use_admin_tools(user)


@register.filter
def can_view_articles(user):
    return user_can_view_articles(user)


@register.filter
def can_add_articles(user):
    return user_can_add_articles(user)


@register.filter
def can_manage_articles(user):
    return user_can_manage_articles(user)


@register.filter
def can_use_admin_tools(user):
    return user_can_use_admin_tools(user)


@register.filter
def can_view_dislike_counts(user):
    return user_can_view_dislike_counts(user)
