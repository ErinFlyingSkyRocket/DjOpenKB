from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def raw_available_languages():
    """Return project language choices exactly as configured in settings.LANGUAGES.

    Django's built-in get_available_languages tag may localise language names
    based on the active page language. DjOpenKB intentionally keeps the
    language dropdown names in their original/native form so users can always
    recognise their own language.
    """
    return list(getattr(settings, "LANGUAGES", []))
