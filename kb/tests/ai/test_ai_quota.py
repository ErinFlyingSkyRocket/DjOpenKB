from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from kb.admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from kb.middleware import ForceLoginAndAdminGuardMiddleware


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    REDIS_URL="",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "openkb-ai-fixed-window-tests",
        }
    },
)
class OpenKBAIFixed24HourQuotaTests(TestCase):
    """Regression coverage for the per-user first-prompt fixed 24-hour cap."""

    def setUp(self):
        from django.core.cache import cache

        from kb.models import SiteSetting
        from kb.permissions import ROLE_REGULAR_USER, assign_single_role_group

        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="ai-fixed-window-test",
            email="ai-fixed-window-test@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.user, ROLE_REGULAR_USER)
        self.client.force_login(self.user)

        setting = SiteSetting.load()
        setting.openkb_ai_prompt_limit_per_24_hours = 2
        setting.save(update_fields=["openkb_ai_prompt_limit_per_24_hours", "updated_at"])

    @property
    def _quota_key(self):
        return f"openkb_ai:quota24h:user:{self.user.pk}"

    def _ask(self, question):
        return self.client.post(reverse("ask_openkb_ai"), {"question": question})

    def test_first_prompt_starts_one_fixed_window_and_later_prompts_do_not_extend_it(self):
        from unittest.mock import patch
        from django.core.cache import cache

        with patch("kb.tasks.run_openkb_ai_job.apply_async"):
            first = self._ask("first valid question")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(cache.get(self._quota_key), 1)

            second = self._ask("second valid question")
            self.assertEqual(second.status_code, 202)
            self.assertEqual(cache.get(self._quota_key), 2)

            third = self._ask("third valid question")

        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["prompt_limit"], 2)
        self.assertEqual(third.json()["prompt_used"], 2)

    def test_invalid_prompt_does_not_start_or_consume_the_window(self):
        from unittest.mock import patch
        from django.core.cache import cache

        blank = self._ask("")
        self.assertEqual(blank.status_code, 400)
        self.assertIsNone(cache.get(self._quota_key))

        with patch("kb.tasks.run_openkb_ai_job.apply_async"):
            accepted = self._ask("first valid question")

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(cache.get(self._quota_key), 1)

    def test_admin_save_invalidates_the_cached_quota_setting(self):
        from django.core.cache import cache
        from kb.models import SiteSetting
        from kb.views.services_ai import get_openkb_ai_prompt_limit_per_24_hours

        self.assertEqual(get_openkb_ai_prompt_limit_per_24_hours(), 2)
        self.assertEqual(cache.get("openkb_ai:quota24h:configured-limit"), 2)

        setting = SiteSetting.load()
        setting.openkb_ai_prompt_limit_per_24_hours = 7
        setting.save(update_fields=["openkb_ai_prompt_limit_per_24_hours", "updated_at"])

        self.assertIsNone(cache.get("openkb_ai:quota24h:configured-limit"))
        self.assertEqual(get_openkb_ai_prompt_limit_per_24_hours(), 7)
