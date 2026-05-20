from django import template

register = template.Library()


@register.filter
def is_site_admin(user):
    """Return True when a user should see DjOpenKB admin navigation items."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    profile = getattr(user, "kb_profile", None)
    return bool(profile and getattr(profile, "is_admin_type", False))
