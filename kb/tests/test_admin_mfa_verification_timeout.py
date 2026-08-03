import pyotp

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from kb.admin_security import (
    ADMIN_MFA_CHALLENGE_EXPIRED_KEY,
    ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY,
    ADMIN_MFA_CHALLENGE_STARTED_AT_KEY,
    ADMIN_MFA_USER_ID_KEY,
    ADMIN_MFA_VERIFIED_KEY,
)
from kb.mfa import get_or_create_mfa_device
from kb.models import SiteSetting


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class AdminMFAVerificationTimeoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin-mfa-timeout-test",
            email="admin-mfa-timeout-test@example.invalid",
            password="Safe-test-password-123!",
        )
        self.device = get_or_create_mfa_device(self.user)
        self.device.confirmed = True
        self.device.save(update_fields=["confirmed"])

        setting = SiteSetting.load()
        setting.mfa_login_timeout_seconds = 90
        setting.admin_mfa_verification_timeout_seconds = 60
        setting.save(
            update_fields=[
                "mfa_login_timeout_seconds",
                "admin_mfa_verification_timeout_seconds",
            ]
        )

        self.client.force_login(self.user)
        self.verify_url = reverse("admin_mfa_verify")
        self.fresh_verify_url = f"{self.verify_url}?next=/admin/&fresh=1"

    def _set_challenge(self, *, started_seconds_ago=0, timeout_seconds=60):
        now = int(timezone.now().timestamp())
        started_at = now - started_seconds_ago
        session = self.client.session
        session[ADMIN_MFA_CHALLENGE_STARTED_AT_KEY] = started_at
        session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY] = started_at + timeout_seconds
        session.save()
        return started_at + timeout_seconds

    def test_admin_mfa_page_shows_seconds_countdown_before_verify_button(self):
        response = self.client.get(self.fresh_verify_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="admin-mfa-countdown"')
        self.assertContains(response, 'data-remaining-seconds="')
        remaining = response.context["admin_mfa_timeout_remaining_seconds"]
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 60)
        self.assertContains(response, f">{remaining}s</strong>")

        rendered = response.content.decode("utf-8")
        code_position = rendered.index('id="id_code"')
        countdown_position = rendered.index('id="admin-mfa-countdown"')
        button_position = rendered.index('type="submit">', countdown_position)
        self.assertLess(code_position, countdown_position)
        self.assertLess(countdown_position, button_position)

    def test_refreshing_admin_mfa_page_does_not_extend_deadline(self):
        first_response = self.client.get(self.fresh_verify_url)
        first_expiry = self.client.session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY]

        second_response = self.client.get(self.fresh_verify_url)
        second_expiry = self.client.session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY]

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_expiry, first_expiry)
        self.assertLessEqual(
            second_response.context["admin_mfa_timeout_remaining_seconds"],
            first_response.context["admin_mfa_timeout_remaining_seconds"],
        )

    def test_leaving_admin_mfa_page_clears_unfinished_challenge(self):
        self.client.get(self.fresh_verify_url)
        self.assertIn(
            ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY,
            self.client.session,
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertNotIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRED_KEY, session)

    def test_admin_timeout_is_separate_from_main_login_timeout(self):
        setting = SiteSetting.load()
        setting.mfa_login_timeout_seconds = 120
        setting.admin_mfa_verification_timeout_seconds = 45
        setting.save(
            update_fields=[
                "mfa_login_timeout_seconds",
                "admin_mfa_verification_timeout_seconds",
            ]
        )

        response = self.client.get(self.fresh_verify_url)

        remaining = response.context["admin_mfa_timeout_remaining_seconds"]
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 45)

    def test_expired_get_keeps_user_signed_in_and_waits_for_retry(self):
        self._set_challenge(started_seconds_ago=61)

        response = self.client.get(f"{self.verify_url}?next=/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Admin MFA verification timed out. Start a new verification window to try again.",
        )
        self.assertContains(response, "Start new verification window")
        self.assertNotContains(response, 'id="admin-mfa-countdown"')
        session = self.client.session
        self.assertIn("_auth_user_id", session)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, session)
        self.assertTrue(session.get(ADMIN_MFA_CHALLENGE_EXPIRED_KEY))
        self.assertNotIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)

    def test_expired_post_rejects_valid_code_and_waits_for_retry(self):
        self._set_challenge(started_seconds_ago=61)
        valid_code = pyotp.TOTP(self.device.get_secret()).now()

        response = self.client.post(
            self.verify_url,
            {"next": "/admin/", "code": valid_code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, self.client.session)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertTrue(self.client.session.get(ADMIN_MFA_CHALLENGE_EXPIRED_KEY))
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, self.client.session)

    def test_early_timeout_post_does_not_extend_active_window(self):
        previous_expiry = self._set_challenge(started_seconds_ago=10)

        response = self.client.post(
            self.verify_url,
            {"next": "/admin/", "action": "timeout"},
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertIn("_auth_user_id", session)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, session)
        self.assertEqual(
            session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY],
            previous_expiry,
        )

    def test_expired_countdown_timeout_post_waits_for_retry_without_logging_out(self):
        self._set_challenge(started_seconds_ago=61)

        response = self.client.post(
            self.verify_url,
            {"next": "/admin/", "action": "timeout"},
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertIn("_auth_user_id", session)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, session)
        self.assertTrue(session.get(ADMIN_MFA_CHALLENGE_EXPIRED_KEY))
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)

    def test_explicit_retry_starts_a_new_fixed_window(self):
        self._set_challenge(started_seconds_ago=61)
        self.client.get(f"{self.verify_url}?next=/admin/")

        response = self.client.post(
            self.verify_url,
            {"next": "/admin/", "action": "restart"},
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertIn("_auth_user_id", session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRED_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)
        self.assertContains(response, 'id="admin-mfa-countdown"')

    def test_successful_admin_mfa_clears_challenge_and_sets_admin_grant(self):
        self.client.get(self.fresh_verify_url)
        valid_code = pyotp.TOTP(self.device.get_secret()).now()

        response = self.client.post(
            self.verify_url,
            {"next": "/admin/", "code": valid_code},
        )

        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)
        session = self.client.session
        self.assertTrue(session.get(ADMIN_MFA_VERIFIED_KEY))
        self.assertEqual(session.get(ADMIN_MFA_USER_ID_KEY), str(self.user.pk))
        self.assertNotIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRED_KEY, session)

    def test_site_setting_admin_places_both_verification_timers_together(self):
        model_admin = admin.site._registry[SiteSetting]
        timeout_fieldset = next(
            options
            for title, options in model_admin.fieldsets
            if str(title) == "MFA verification completion timeouts"
        )
        flattened_fields = {
            field_name
            for row in timeout_fieldset["fields"]
            for field_name in (row if isinstance(row, tuple) else (row,))
        }

        self.assertIn("mfa_login_timeout_seconds", flattened_fields)
        self.assertIn("admin_mfa_verification_timeout_seconds", flattened_fields)
        self.assertIn("admin_mfa_verification_timeout_display", flattened_fields)
        self.assertNotIn(
            "admin_mfa_verification_timeout_seconds",
            model_admin.readonly_fields,
        )
        self.assertEqual(model_admin.list_display, ("__str__",))
