from django.conf import settings
from django.utils import translation

from .models import UserProfile


class UserProfileLanguageMiddleware:
    """Activate the logged-in user's saved UI language.

    Django's LocaleMiddleware handles normal language detection first. This
    middleware then gives the user's profile preference priority after
    AuthenticationMiddleware has attached request.user.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            language_code = profile.preferred_language or settings.LANGUAGE_CODE

            allowed_codes = {code for code, _name in settings.LANGUAGES}
            if language_code not in allowed_codes:
                language_code = settings.LANGUAGE_CODE

            translation.activate(language_code)
            request.LANGUAGE_CODE = language_code

        response = self.get_response(request)

        if user and user.is_authenticated:
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, request.LANGUAGE_CODE)

        return response
