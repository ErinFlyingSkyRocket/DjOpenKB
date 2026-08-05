from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings

from kb.http_cookies import set_language_cookie


class LanguageCookieSecurityTests(SimpleTestCase):
    @override_settings(
        LANGUAGE_COOKIE_NAME="django_language",
        LANGUAGE_COOKIE_AGE=3600,
        LANGUAGE_COOKIE_PATH="/",
        LANGUAGE_COOKIE_DOMAIN=None,
        LANGUAGE_COOKIE_SECURE=True,
        LANGUAGE_COOKIE_HTTPONLY=True,
        LANGUAGE_COOKIE_SAMESITE="Lax",
    )
    def test_language_cookie_uses_explicit_security_attributes(self):
        response = set_language_cookie(HttpResponse(), "en")
        cookie = response.cookies["django_language"]

        self.assertEqual(cookie.value, "en")
        self.assertEqual(cookie["path"], "/")
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(int(cookie["max-age"]), 3600)
