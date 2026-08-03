from datetime import timedelta
import secrets
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from kb.mfa import (
    PRE_MFA_BACKEND_SESSION_KEY,
    PRE_MFA_CHALLENGE_ID_SESSION_KEY,
    PRE_MFA_EXPIRES_AT_SESSION_KEY,
    PRE_MFA_NEXT_SESSION_KEY,
    PRE_MFA_STARTED_AT_SESSION_KEY,
    PRE_MFA_USER_ID_SESSION_KEY,
    get_or_create_mfa_device,
    get_totp_valid_window,
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
        session[PRE_MFA_CHALLENGE_ID_SESSION_KEY] = secrets.token_urlsafe(24)
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
        self.assertContains(first_response, 'data-challenge-id="')
        first_remaining = first_response.context["mfa_login_timeout_remaining_seconds"]
        expected_display = f"{first_remaining}s"
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
        self.assertTrue(session.get(PRE_MFA_CHALLENGE_ID_SESSION_KEY))

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
        self.assertNotIn(PRE_MFA_CHALLENGE_ID_SESSION_KEY, session)

    def test_expired_mfa_post_cannot_complete_login(self):
        self._set_pending_login(seconds_ago=61)

        response = self.client.post(reverse("mfa_verify"), {"code": "000000"})

        self.assertRedirects(response, reverse("root_login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_countdown_timeout_post_clears_pending_login(self):
        self._set_pending_login(seconds_ago=10)

        response = self.client.post(
            reverse("mfa_cancel"),
            {
                "reason": "timeout",
                "challenge_id": self.client.session[PRE_MFA_CHALLENGE_ID_SESSION_KEY],
            },
        )

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
        self.assertTrue(session.get(PRE_MFA_CHALLENGE_ID_SESSION_KEY))

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

    def test_confirmed_unreadable_mfa_secret_is_not_silently_replaced(self):
        self.device.secret = "fernet$invalid-confirmed-secret"
        self.device.confirmed = True
        self.device.save(update_fields=["secret", "confirmed"])
        original_encrypted_value = self.device.secret

        device = get_or_create_mfa_device(self.user)

        self.assertTrue(device.confirmed)
        self.assertEqual(device.secret, original_encrypted_value)
        self.assertFalse(device.get_secret())

    def test_unconfirmed_unreadable_mfa_secret_is_regenerated_for_setup(self):
        self.device.secret = "fernet$invalid-unconfirmed-secret"
        self.device.confirmed = False
        self.device.save(update_fields=["secret", "confirmed"])
        original_encrypted_value = self.device.secret

        device = get_or_create_mfa_device(self.user)

        self.assertFalse(device.confirmed)
        self.assertNotEqual(device.secret, original_encrypted_value)
        self.assertTrue(device.get_secret())


    def test_stale_pending_mfa_tab_cannot_cancel_newer_challenge(self):
        self._set_pending_login(seconds_ago=5)
        stale_challenge = self.client.session[PRE_MFA_CHALLENGE_ID_SESSION_KEY]

        session = self.client.session
        current_challenge = secrets.token_urlsafe(24)
        session[PRE_MFA_CHALLENGE_ID_SESSION_KEY] = current_challenge
        session.save()

        response = self.client.post(
            reverse("mfa_cancel"),
            {"reason": "timeout", "challenge_id": stale_challenge},
        )

        self.assertRedirects(response, reverse("mfa_verify"), fetch_redirect_response=False)
        self.assertEqual(
            self.client.session.get(PRE_MFA_CHALLENGE_ID_SESSION_KEY),
            current_challenge,
        )
        self.assertIn(PRE_MFA_USER_ID_SESSION_KEY, self.client.session)

    def test_stale_pending_mfa_tab_cannot_submit_code_to_newer_challenge(self):
        self._set_pending_login(seconds_ago=5)
        stale_challenge = self.client.session[PRE_MFA_CHALLENGE_ID_SESSION_KEY]

        session = self.client.session
        current_challenge = secrets.token_urlsafe(24)
        session[PRE_MFA_CHALLENGE_ID_SESSION_KEY] = current_challenge
        session.save()

        with patch("kb.views.mfa.verify_totp_code") as verify_mock:
            response = self.client.post(
                reverse("mfa_verify"),
                {"code": "000000", "challenge_id": stale_challenge},
            )

        self.assertRedirects(response, reverse("mfa_verify"), fetch_redirect_response=False)
        verify_mock.assert_not_called()
        self.assertEqual(
            self.client.session.get(PRE_MFA_CHALLENGE_ID_SESSION_KEY),
            current_challenge,
        )
        self.assertIn(PRE_MFA_USER_ID_SESSION_KEY, self.client.session)

    @override_settings(MFA_TOTP_VALID_WINDOW="invalid")
    def test_invalid_totp_window_setting_falls_back_to_one(self):
        self.assertEqual(get_totp_valid_window(), 1)

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
