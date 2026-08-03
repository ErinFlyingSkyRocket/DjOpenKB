from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from kb.mfa import (
    PRE_MFA_BACKEND_SESSION_KEY,
    PRE_MFA_NEXT_SESSION_KEY,
    PRE_MFA_STARTED_AT_SESSION_KEY,
    PRE_MFA_USER_ID_SESSION_KEY,
    get_or_create_mfa_device,
)
from kb.models import SiteSetting


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class MFALoginTimeoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mfa-timeout-user",
            email="mfa-timeout-user@example.invalid",
            password="Safe-test-password-123!",
        )
        self.device = get_or_create_mfa_device(self.user)
        self.device.confirmed = True
        self.device.save(update_fields=["confirmed"])

        site_setting = SiteSetting.load()
        site_setting.mfa_login_timeout_seconds = 60
        site_setting.save(update_fields=["mfa_login_timeout_seconds"])

    def _set_pending_login(self, *, seconds_ago=0):
        session = self.client.session
        session[PRE_MFA_USER_ID_SESSION_KEY] = str(self.user.pk)
        session[PRE_MFA_BACKEND_SESSION_KEY] = "kb.backends.EmailOrUsernameModelBackend"
        session[PRE_MFA_NEXT_SESSION_KEY] = reverse("home")
        session[PRE_MFA_STARTED_AT_SESSION_KEY] = (
            timezone.now() - timedelta(seconds=seconds_ago)
        ).isoformat()
        session.save()

    def test_mfa_page_shows_countdown_without_resetting_the_deadline(self):
        self._set_pending_login(seconds_ago=10)

        original_started_at = self.client.session.get(PRE_MFA_STARTED_AT_SESSION_KEY)
        first_response = self.client.get(reverse("mfa_verify"))
        second_response = self.client.get(reverse("mfa_verify"))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertContains(first_response, 'id="mfa-login-countdown"')
        self.assertContains(first_response, 'id="mfa-timeout-form"')

        first_remaining = first_response.context["mfa_login_timeout_remaining_seconds"]
        second_remaining = second_response.context["mfa_login_timeout_remaining_seconds"]
        self.assertGreater(first_remaining, 0)
        self.assertLessEqual(second_remaining, first_remaining)
        self.assertEqual(
            self.client.session.get(PRE_MFA_STARTED_AT_SESSION_KEY),
            original_started_at,
        )

    def test_expired_mfa_page_clears_pending_login_and_requires_password_again(self):
        self._set_pending_login(seconds_ago=61)

        response = self.client.get(reverse("mfa_verify"))

        self.assertRedirects(response, reverse("root_login"), fetch_redirect_response=False)
        session = self.client.session
        self.assertNotIn(PRE_MFA_USER_ID_SESSION_KEY, session)
        self.assertNotIn(PRE_MFA_BACKEND_SESSION_KEY, session)
        self.assertNotIn(PRE_MFA_NEXT_SESSION_KEY, session)
        self.assertNotIn(PRE_MFA_STARTED_AT_SESSION_KEY, session)

    def test_expired_mfa_post_cannot_complete_login(self):
        self._set_pending_login(seconds_ago=61)

        response = self.client.post(reverse("mfa_verify"), {"code": "000000"})

        self.assertRedirects(response, reverse("root_login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_countdown_timeout_post_clears_pending_login(self):
        self._set_pending_login(seconds_ago=10)

        response = self.client.post(reverse("mfa_cancel"), {"reason": "timeout"})

        self.assertRedirects(response, reverse("root_login"), fetch_redirect_response=False)
        self.assertNotIn(PRE_MFA_USER_ID_SESSION_KEY, self.client.session)
