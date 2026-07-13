"""Regression tests for the browser/session-wide password cooldown."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from kb.models import AuthLockoutPolicyStage, SiteSetting


class BrowserLoginLockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        setting = SiteSetting.load()
        setting.auth_lockout_stages.all().delete()
        AuthLockoutPolicyStage.objects.create(
            site_setting=setting,
            sort_order=10,
            failure_limit=2,
            block_seconds=300,
            repeat_count=0,
            enabled=True,
        )
        self.user = get_user_model().objects.create_user(
            username="browser-lockout-user",
            email="browser-lockout-user@example.invalid",
            password="safe-test-password",
        )

    @staticmethod
    def _login_payload(username, password):
        return {
            "username": username,
            "password": password,
            "login_mode": "local",
        }

    def test_switching_username_does_not_bypass_same_browser_cooldown(self):
        first_browser = Client()

        first_browser.post(
            reverse("login"),
            self._login_payload("unknown-user-one", "incorrect-password"),
        )
        response = first_browser.post(
            reverse("login"),
            self._login_payload("unknown-user-two", "incorrect-password"),
            follow=True,
        )
        self.assertContains(response, "Too many failed sign-in attempts")

        blocked_valid_login = first_browser.post(
            reverse("login"),
            self._login_payload(self.user.username, "safe-test-password"),
            follow=True,
        )
        self.assertContains(
            blocked_valid_login,
            "Too many failed sign-in attempts",
        )
        self.assertNotIn("_auth_user_id", first_browser.session)

    def test_different_browser_on_same_ip_is_not_blocked(self):
        first_browser = Client(REMOTE_ADDR="192.0.2.50")
        second_browser = Client(REMOTE_ADDR="192.0.2.50")

        first_browser.post(
            reverse("login"),
            self._login_payload("unknown-user-one", "incorrect-password"),
        )
        first_browser.post(
            reverse("login"),
            self._login_payload("unknown-user-two", "incorrect-password"),
        )

        response = second_browser.post(
            reverse("login"),
            self._login_payload(self.user.username, "safe-test-password"),
        )
        self.assertNotContains(
            response,
            "Too many failed sign-in attempts",
            status_code=response.status_code,
        )
