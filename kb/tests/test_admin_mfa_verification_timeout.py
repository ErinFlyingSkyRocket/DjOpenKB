import pyotp
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from kb.admin_security import (
    ADMIN_MFA_CHALLENGE_EXPIRED_KEY,
    ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY,
    ADMIN_MFA_CHALLENGE_ID_KEY,
    ADMIN_MFA_CHALLENGE_STARTED_AT_KEY,
    ADMIN_MFA_LOCKOUT_DEFERRED_KEY,
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
        self.start_url = reverse("admin_mfa_start")
        self.canonical_verify_url = (
            f"{self.verify_url}?{urlencode({'next': '/admin/'})}"
        )
        self.entry_verify_url = f"{self.verify_url}?next=/admin/&entry=1"

    def _set_challenge(
        self,
        *,
        started_seconds_ago=0,
        timeout_seconds=60,
        challenge_id="test-admin-mfa-challenge",
    ):
        now = int(timezone.now().timestamp())
        started_at = now - started_seconds_ago
        expires_at = started_at + timeout_seconds
        session = self.client.session
        session[ADMIN_MFA_CHALLENGE_ID_KEY] = challenge_id
        session[ADMIN_MFA_CHALLENGE_STARTED_AT_KEY] = started_at
        session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY] = expires_at
        session.save()
        return challenge_id, expires_at

    def _start_fresh_challenge(self):
        entry_response = self.client.get(self.entry_verify_url)
        self.assertRedirects(
            entry_response,
            self.canonical_verify_url,
            fetch_redirect_response=False,
        )
        return self.client.get(self.canonical_verify_url)

    def _current_challenge_id(self):
        return self.client.session[ADMIN_MFA_CHALLENGE_ID_KEY]

    def _assert_challenge_cleared(self):
        session = self.client.session
        self.assertNotIn(ADMIN_MFA_CHALLENGE_ID_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRED_KEY, session)
        self.assertNotIn(ADMIN_MFA_LOCKOUT_DEFERRED_KEY, session)

    def test_admin_mfa_start_endpoint_rejects_get_without_rotating_state(self):
        old_id, old_expiry = self._set_challenge(challenge_id="old-challenge")

        response = self.client.get(self.start_url)

        self.assertEqual(response.status_code, 405)
        session = self.client.session
        self.assertEqual(session[ADMIN_MFA_CHALLENGE_ID_KEY], old_id)
        self.assertEqual(session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY], old_expiry)

    def test_entry_redirect_starts_fresh_challenge_without_start_button(self):
        old_id, _old_expiry = self._set_challenge(challenge_id="old-challenge")

        response = self.client.get(self.entry_verify_url)

        self.assertRedirects(
            response,
            self.canonical_verify_url,
            fetch_redirect_response=False,
        )
        session = self.client.session
        self.assertNotEqual(session[ADMIN_MFA_CHALLENGE_ID_KEY], old_id)
        self.assertIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)

        verify_response = self.client.get(self.canonical_verify_url)
        self.assertContains(verify_response, 'id="id_code"')
        self.assertContains(verify_response, 'autofocus')
        self.assertNotContains(verify_response, "Start new verification window")
        self.assertNotContains(verify_response, f'action="{self.start_url}"')

    def test_post_start_remains_backward_compatible_and_rotates_challenge(self):
        old_id, _old_expiry = self._set_challenge(challenge_id="old-challenge")

        response = self.client.post(self.start_url, {"next": "/admin/"})

        self.assertRedirects(
            response,
            self.canonical_verify_url,
            fetch_redirect_response=False,
        )
        session = self.client.session
        self.assertNotEqual(session[ADMIN_MFA_CHALLENGE_ID_KEY], old_id)
        self.assertIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRED_KEY, session)

    def test_admin_mfa_page_shows_otp_and_seconds_countdown(self):
        response = self._start_fresh_challenge()
        challenge_id = self._current_challenge_id()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_code"')
        self.assertContains(response, 'id="admin-mfa-countdown"')
        self.assertContains(response, 'data-remaining-seconds="')
        self.assertContains(response, f'data-challenge-id="{challenge_id}"')
        self.assertContains(
            response,
            f'name="challenge_id" value="{challenge_id}"',
        )
        remaining = response.context["admin_mfa_timeout_remaining_seconds"]
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 60)
        self.assertContains(response, f">{remaining}s</strong>")

        rendered = response.content.decode("utf-8")
        code_position = rendered.index('id="id_code"')
        countdown_position = rendered.index('id="admin-mfa-countdown"')
        button_position = rendered.index('type="submit"', countdown_position)
        self.assertLess(code_position, countdown_position)
        self.assertLess(countdown_position, button_position)

    def test_refreshing_canonical_admin_mfa_page_does_not_extend_or_rotate(self):
        first_response = self._start_fresh_challenge()
        first_session = self.client.session
        first_id = first_session[ADMIN_MFA_CHALLENGE_ID_KEY]
        first_expiry = first_session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY]

        second_response = self.client.get(self.canonical_verify_url)
        second_session = self.client.session

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_session[ADMIN_MFA_CHALLENGE_ID_KEY], first_id)
        self.assertEqual(
            second_session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY],
            first_expiry,
        )
        self.assertLessEqual(
            second_response.context["admin_mfa_timeout_remaining_seconds"],
            first_response.context["admin_mfa_timeout_remaining_seconds"],
        )

    def test_a_new_admin_entry_replaces_the_previous_pending_challenge(self):
        self._start_fresh_challenge()
        first_id = self._current_challenge_id()

        response = self.client.get(self.entry_verify_url)
        self.assertRedirects(
            response,
            self.canonical_verify_url,
            fetch_redirect_response=False,
        )
        second_id = self._current_challenge_id()

        self.assertNotEqual(second_id, first_id)

    def test_leaving_admin_mfa_page_clears_unfinished_challenge(self):
        self._start_fresh_challenge()
        self.assertIn(ADMIN_MFA_CHALLENGE_ID_KEY, self.client.session)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self._assert_challenge_cleared()

    def test_active_admin_mfa_lockout_defers_the_short_verification_timer(self):
        with patch(
            "kb.admin_security.get_auth_lockout_status",
            return_value=(True, 300, "admin_mfa:user:test"),
        ):
            entry_response = self.client.get(self.entry_verify_url)
            self.assertRedirects(
                entry_response,
                self.canonical_verify_url,
                fetch_redirect_response=False,
            )
            locked_response = self.client.get(self.canonical_verify_url)

        self.assertTrue(locked_response.context["admin_mfa_rate_limit_active"])
        self.assertFalse(locked_response.context["admin_mfa_timeout_active"])
        self.assertContains(locked_response, 'id="id_code"')
        session = self.client.session
        self.assertTrue(session.get(ADMIN_MFA_LOCKOUT_DEFERRED_KEY))
        self.assertNotIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)

        with patch(
            "kb.admin_security.get_auth_lockout_status",
            return_value=(False, 0, "admin_mfa:user:test"),
        ):
            resumed_response = self.client.get(self.canonical_verify_url)

        self.assertFalse(resumed_response.context["admin_mfa_rate_limit_active"])
        self.assertTrue(resumed_response.context["admin_mfa_timeout_active"])
        session = self.client.session
        self.assertNotIn(ADMIN_MFA_LOCKOUT_DEFERRED_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)

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

        response = self._start_fresh_challenge()

        remaining = response.context["admin_mfa_timeout_remaining_seconds"]
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 45)

    def test_expired_get_redirects_home_and_clears_pending_challenge(self):
        self._set_challenge(started_seconds_ago=61)

        response = self.client.get(self.canonical_verify_url)

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, self.client.session)
        self._assert_challenge_cleared()

    def test_expired_post_rejects_valid_code_and_redirects_home(self):
        challenge_id, _expiry = self._set_challenge(started_seconds_ago=61)
        valid_code = pyotp.TOTP(self.device.get_secret()).now()

        response = self.client.post(
            self.verify_url,
            {
                "next": "/admin/",
                "challenge_id": challenge_id,
                "code": valid_code,
            },
        )

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, self.client.session)
        self.assertIn("_auth_user_id", self.client.session)
        self._assert_challenge_cleared()

    def test_early_timeout_post_does_not_extend_active_window(self):
        challenge_id, previous_expiry = self._set_challenge(started_seconds_ago=10)

        response = self.client.post(
            self.verify_url,
            {
                "next": "/admin/",
                "challenge_id": challenge_id,
                "action": "timeout",
            },
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertIn("_auth_user_id", session)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, session)
        self.assertEqual(session[ADMIN_MFA_CHALLENGE_ID_KEY], challenge_id)
        self.assertEqual(
            session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY],
            previous_expiry,
        )

    def test_expired_countdown_post_redirects_home_and_clears_challenge(self):
        challenge_id, _expiry = self._set_challenge(started_seconds_ago=61)

        response = self.client.post(
            self.verify_url,
            {
                "next": "/admin/",
                "challenge_id": challenge_id,
                "action": "timeout",
            },
        )

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, self.client.session)
        self._assert_challenge_cleared()

    def test_legacy_expired_state_automatically_opens_a_fresh_otp_window(self):
        old_id, _expiry = self._set_challenge(challenge_id="legacy-expired")
        session = self.client.session
        session.pop(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, None)
        session.pop(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, None)
        session[ADMIN_MFA_CHALLENGE_EXPIRED_KEY] = True
        session.save()

        response = self.client.get(self.canonical_verify_url)

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertNotEqual(session[ADMIN_MFA_CHALLENGE_ID_KEY], old_id)
        self.assertNotIn(ADMIN_MFA_CHALLENGE_EXPIRED_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, session)
        self.assertIn(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, session)
        self.assertContains(response, 'id="id_code"')
        self.assertNotContains(response, "Start new verification window")

    def test_stale_page_cannot_verify_after_a_new_entry_rotates_challenge(self):
        self._start_fresh_challenge()
        stale_challenge_id = self._current_challenge_id()

        self.client.get(self.entry_verify_url)
        current_challenge_id = self._current_challenge_id()
        valid_code = pyotp.TOTP(self.device.get_secret()).now()

        response = self.client.post(
            self.verify_url,
            {
                "next": "/admin/",
                "challenge_id": stale_challenge_id,
                "code": valid_code,
            },
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertNotIn(ADMIN_MFA_VERIFIED_KEY, session)
        self.assertEqual(
            session[ADMIN_MFA_CHALLENGE_ID_KEY],
            current_challenge_id,
        )
        self.assertContains(
            response,
            f'name="challenge_id" value="{current_challenge_id}"',
        )

    def test_successful_admin_mfa_clears_challenge_and_sets_admin_grant(self):
        self._start_fresh_challenge()
        challenge_id = self._current_challenge_id()
        valid_code = pyotp.TOTP(self.device.get_secret()).now()
        old_session_key = self.client.session.session_key

        response = self.client.post(
            self.verify_url,
            {
                "next": "/admin/",
                "challenge_id": challenge_id,
                "code": valid_code,
            },
        )

        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)
        session = self.client.session
        self.assertNotEqual(session.session_key, old_session_key)
        self.assertTrue(session.get(ADMIN_MFA_VERIFIED_KEY))
        self.assertEqual(session.get(ADMIN_MFA_USER_ID_KEY), str(self.user.pk))
        self._assert_challenge_cleared()

    def test_site_setting_admin_describes_automatic_otp_and_timeout_redirect(self):
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
        self.assertIn("OTP field opens automatically", str(timeout_fieldset["description"]))
        self.assertIn("returns the administrator to the normal site", str(timeout_fieldset["description"]))
        self.assertEqual(model_admin.list_display, ("__str__",))
