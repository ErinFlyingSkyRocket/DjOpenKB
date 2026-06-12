from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def configured_language_choices():
    """Return language names exactly as defined in settings.LANGUAGES.

    This keeps the language dropdown stable instead of translating language
    names into the currently selected language.
    """
    return list(settings.LANGUAGES)
