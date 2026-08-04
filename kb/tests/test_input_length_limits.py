from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from kb.admin import (
    SiteSettingAdminForm,
    SuggestedArticleAdminForm,
    UniqueEmailUserChangeForm,
    UniqueEmailUserCreationForm,
    UserProfileInlineForm,
)
from kb.input_limits import (
    ADMIN_ALLOWED_CIDRS_MAX_LENGTH,
    GENERIC_TEXT_INPUT_MAX_LENGTH,
    LOGIN_IDENTIFIER_MAX_LENGTH,
    MFA_CODE_MAX_LENGTH,
    PASSWORD_MAX_LENGTH,
    PROFILE_NOTES_MAX_LENGTH,
    REVIEW_NOTES_MAX_LENGTH,
    SEARCH_QUERY_MAX_LENGTH,
    URL_MAX_LENGTH,
    get_field_character_limit,
    template_input_limits,
)
from kb.middleware import InputLengthLimitMiddleware


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class InputLengthLimitMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.downstream_calls = 0

        def downstream(_request):
            self.downstream_calls += 1
            return HttpResponse("ok")

        self.middleware = InputLengthLimitMiddleware(downstream)

    @staticmethod
    def _prepare_request(request):
        request.user = AnonymousUser()
        request.csp_nonce = "test-nonce"
        return request

    def test_search_query_at_limit_reaches_view(self):
        request = self._prepare_request(
            self.factory.get("/search/", {"q": "a" * SEARCH_QUERY_MAX_LENGTH})
        )

        response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertEqual(self.downstream_calls, 1)

    def test_overlong_search_query_is_rejected_before_view(self):
        request = self._prepare_request(
            self.factory.get(
                "/search/",
                {"q": "a" * (SEARCH_QUERY_MAX_LENGTH + 1)},
            )
        )

        response = self.middleware(request)

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            f"maximum of {SEARCH_QUERY_MAX_LENGTH} characters",
            status_code=400,
        )
        self.assertEqual(self.downstream_calls, 0)

    def test_overlong_json_endpoint_value_returns_json_error(self):
        request = self._prepare_request(
            self.factory.get(
                "/search/suggestions/",
                {"q": "a" * (SEARCH_QUERY_MAX_LENGTH + 1)},
            )
        )

        response = self.middleware(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.json()["field"], "q")
        self.assertEqual(response.json()["max_length"], SEARCH_QUERY_MAX_LENGTH)
        self.assertEqual(self.downstream_calls, 0)

    def test_overlong_password_is_rejected_for_urlencoded_form(self):
        encoded = urlencode({"password": "p" * (PASSWORD_MAX_LENGTH + 1)})
        request = self._prepare_request(
            self.factory.post(
                "/login/",
                data=encoded,
                content_type="application/x-www-form-urlencoded",
            )
        )

        response = self.middleware(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.downstream_calls, 0)

    def test_unknown_text_field_uses_safe_generic_limit(self):
        encoded = urlencode(
            {"future_text_field": "x" * (GENERIC_TEXT_INPUT_MAX_LENGTH + 1)}
        )
        request = self._prepare_request(
            self.factory.post(
                "/future-form/",
                data=encoded,
                content_type="application/x-www-form-urlencoded",
            )
        )

        response = self.middleware(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.downstream_calls, 0)

    def test_article_body_uses_configured_dynamic_limit(self):
        encoded = urlencode({"frm_kb_body": "x" * 1001})
        request = self._prepare_request(
            self.factory.post(
                "/suggest/",
                data=encoded,
                content_type="application/x-www-form-urlencoded",
            )
        )

        with patch("kb.models.get_article_body_character_limit", return_value=1000):
            response = self.middleware(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.downstream_calls, 0)


class InputLimitDefinitionTests(SimpleTestCase):
    def test_security_sensitive_limits_are_expected(self):
        self.assertEqual(MFA_CODE_MAX_LENGTH, 32)
        self.assertEqual(PASSWORD_MAX_LENGTH, 256)
        self.assertEqual(LOGIN_IDENTIFIER_MAX_LENGTH, 254)
        self.assertEqual(SEARCH_QUERY_MAX_LENGTH, 200)
        self.assertEqual(URL_MAX_LENGTH, 2048)
        self.assertEqual(REVIEW_NOTES_MAX_LENGTH, 4000)

    @override_settings(OPENKB_AI_MAX_PROMPT_CHARS=1500)
    def test_ai_question_limit_follows_safe_setting(self):
        self.assertEqual(get_field_character_limit("question"), 1500)
        self.assertEqual(template_input_limits()["ai_question"], 1500)

    def test_unknown_fields_are_always_bounded(self):
        self.assertEqual(
            get_field_character_limit("new_future_field"),
            GENERIC_TEXT_INPUT_MAX_LENGTH,
        )


class AdminInputLimitFormTests(SimpleTestCase):
    def test_profile_notes_widget_has_limit(self):
        form = UserProfileInlineForm()
        self.assertEqual(
            form.fields["notes"].widget.attrs["maxlength"],
            PROFILE_NOTES_MAX_LENGTH,
        )
        self.assertEqual(form.fields["notes"].max_length, PROFILE_NOTES_MAX_LENGTH)

    def test_user_admin_password_widgets_have_limit(self):
        creation_form = UniqueEmailUserCreationForm()
        self.assertEqual(
            creation_form.fields["password1"].widget.attrs["maxlength"],
            PASSWORD_MAX_LENGTH,
        )
        self.assertEqual(
            creation_form.fields["password2"].widget.attrs["maxlength"],
            PASSWORD_MAX_LENGTH,
        )

        change_form = UniqueEmailUserChangeForm()
        self.assertEqual(
            change_form.fields["password"].widget.attrs["maxlength"],
            PASSWORD_MAX_LENGTH,
        )

    def test_article_review_notes_widget_has_limit_without_reducing_body_limit(self):
        with patch("kb.admin.get_article_body_character_limit", return_value=200000):
            form = SuggestedArticleAdminForm()

        self.assertEqual(
            form.fields["review_notes"].widget.attrs["maxlength"],
            REVIEW_NOTES_MAX_LENGTH,
        )
        self.assertEqual(
            form.fields["review_notes"].max_length,
            REVIEW_NOTES_MAX_LENGTH,
        )
        self.assertEqual(form.fields["body"].widget.attrs["maxlength"], 200000)
        self.assertEqual(
            form.fields["pending_update_body"].widget.attrs["maxlength"],
            200000,
        )

    def test_admin_cidr_widget_has_limit(self):
        form = SiteSettingAdminForm()
        self.assertEqual(
            form.fields["admin_allowed_cidrs"].widget.attrs["maxlength"],
            ADMIN_ALLOWED_CIDRS_MAX_LENGTH,
        )
        self.assertEqual(
            form.fields["admin_allowed_cidrs"].max_length,
            ADMIN_ALLOWED_CIDRS_MAX_LENGTH,
        )


class CustomTemplateInputLimitAuditTests(SimpleTestCase):
    """Keep every custom visible text-like field protected by ``maxlength``."""

    def test_all_custom_text_inputs_and_textareas_have_maxlength(self):
        templates_root = Path(settings.BASE_DIR) / "website" / "templates"
        visible_types = {"text", "search", "email", "password", "url", "tel"}
        missing = []

        # A lightweight source audit avoids adding an HTML parser dependency to
        # the production requirements. Each custom input/textarea is currently
        # written on one logical tag, including multiline tags.
        import re

        tag_pattern = re.compile(r"<(input|textarea)\b[^>]*>", re.IGNORECASE | re.DOTALL)
        type_pattern = re.compile(r'\btype\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

        for template_path in sorted(templates_root.rglob("*.html")):
            source = template_path.read_text(encoding="utf-8")
            for match in tag_pattern.finditer(source):
                tag = match.group(0)
                tag_name = match.group(1).lower()
                type_match = type_pattern.search(tag)
                input_type = (
                    type_match.group(1).lower()
                    if type_match
                    else ("textarea" if tag_name == "textarea" else "text")
                )
                if tag_name == "textarea" or input_type in visible_types:
                    if "maxlength=" not in tag.lower():
                        line_number = source.count("\n", 0, match.start()) + 1
                        missing.append(f"{template_path.relative_to(settings.BASE_DIR)}:{line_number}")

        self.assertEqual(missing, [], "Missing maxlength on: " + ", ".join(missing))
