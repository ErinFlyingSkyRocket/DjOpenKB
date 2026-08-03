from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from kb.mfa import (
    PRE_MFA_BACKEND_SESSION_KEY,
    PRE_MFA_EXPIRES_AT_SESSION_KEY,
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
        started_at = timezone.now() - timedelta(seconds=seconds_ago)
        session[PRE_MFA_STARTED_AT_SESSION_KEY] = started_at.isoformat()
        session[PRE_MFA_EXPIRES_AT_SESSION_KEY] = (
            started_at + timedelta(seconds=60)
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
        self.assertNotContains(first_response, 'id="mfa-timeout-form"')
        self.assertContains(first_response, 'data-cancel-url="')
        first_remaining = first_response.context["mfa_login_timeout_remaining_seconds"]
        expected_display = f"{first_remaining // 60:02d}:{first_remaining % 60:02d}"
        self.assertEqual(
            first_response.context["mfa_login_timeout_remaining_display"],
            expected_display,
        )
        self.assertContains(first_response, f">{expected_display}</strong>")

        rendered = first_response.content.decode("utf-8")
        code_position = rendered.index('id="id_code"')
        countdown_position = rendered.index('id="mfa-login-countdown"')
        verify_position = rendered.index(
            'class="btn btn-lg btn-primary btn-block"',
            countdown_position,
        )
        self.assertLess(code_position, countdown_position)
        self.assertLess(countdown_position, verify_position)

        second_remaining = second_response.context["mfa_login_timeout_remaining_seconds"]
        self.assertGreater(first_remaining, 0)
        self.assertLessEqual(second_remaining, first_remaining)
        self.assertEqual(
            self.client.session.get(PRE_MFA_STARTED_AT_SESSION_KEY),
            original_started_at,
        )

    def test_authenticated_unverified_session_is_converted_and_shows_countdown(self):
        self.client.force_login(self.user)
        session = self.client.session
        session.pop(PRE_MFA_USER_ID_SESSION_KEY, None)
        session.pop(PRE_MFA_BACKEND_SESSION_KEY, None)
        session.pop(PRE_MFA_NEXT_SESSION_KEY, None)
        session.pop(PRE_MFA_STARTED_AT_SESSION_KEY, None)
        session.pop(PRE_MFA_EXPIRES_AT_SESSION_KEY, None)
        session.save()

        response = self.client.get(reverse("mfa_verify"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mfa-login-countdown"')
        session = self.client.session
        self.assertNotIn("_auth_user_id", session)
        self.assertEqual(session.get(PRE_MFA_USER_ID_SESSION_KEY), str(self.user.pk))
        self.assertTrue(session.get(PRE_MFA_STARTED_AT_SESSION_KEY))
        self.assertTrue(session.get(PRE_MFA_EXPIRES_AT_SESSION_KEY))

    def test_expired_mfa_page_clears_pending_login_and_requires_password_again(self):
        self._set_pending_login(seconds_ago=61)

        response = self.client.get(reverse("mfa_verify"))

        self.assertRedirects(response, reverse("root_login"), fetch_redirect_response=False)
        session = self.client.session
        self.assertNotIn(PRE_MFA_USER_ID_SESSION_KEY, session)
        self.assertNotIn(PRE_MFA_BACKEND_SESSION_KEY, session)
        self.assertNotIn(PRE_MFA_NEXT_SESSION_KEY, session)
        self.assertNotIn(PRE_MFA_STARTED_AT_SESSION_KEY, session)
        self.assertNotIn(PRE_MFA_EXPIRES_AT_SESSION_KEY, session)

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

    def test_password_login_creates_fixed_deadline_and_renders_countdown(self):
        response = self.client.post(
            reverse("root_login"),
            {
                "username": self.user.username,
                "password": "Safe-test-password-123!",
                "login_mode": "local",
            },
        )

        self.assertRedirects(
            response,
            reverse("mfa_verify"),
            fetch_redirect_response=False,
        )
        session = self.client.session
        self.assertTrue(session.get(PRE_MFA_STARTED_AT_SESSION_KEY))
        self.assertTrue(session.get(PRE_MFA_EXPIRES_AT_SESSION_KEY))

        verify_response = self.client.get(reverse("mfa_verify"))
        self.assertEqual(verify_response.status_code, 200)
        self.assertContains(verify_response, 'id="mfa-login-countdown"')
        self.assertContains(verify_response, 'data-remaining-seconds="')

    def test_site_setting_change_does_not_extend_active_pending_login(self):
        self._set_pending_login(seconds_ago=61)
        setting = SiteSetting.load()
        setting.mfa_login_timeout_seconds = 900
        setting.save(update_fields=["mfa_login_timeout_seconds"])

        response = self.client.get(reverse("mfa_verify"))

        self.assertRedirects(
            response,
            reverse("root_login"),
            fetch_redirect_response=False,
        )
        self.assertNotIn(PRE_MFA_USER_ID_SESSION_KEY, self.client.session)

    def test_site_setting_admin_exposes_mfa_timeout_control(self):
        model_admin = admin.site._registry[SiteSetting]
        field_names = {
            field_name
            for _title, options in model_admin.fieldsets
            for field_name in options.get("fields", ())
        }

        self.assertIn("mfa_login_timeout_seconds", field_names)
        self.assertNotIn(
            "mfa_login_timeout_seconds",
            model_admin.readonly_fields,
        )
        self.assertNotIn("mfa_login_timeout_seconds", model_admin.list_display)
        self.assertNotIn("session_timeout_hours", model_admin.list_display)
        self.assertEqual(model_admin.list_display, ("__str__",))
