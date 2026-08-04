from unittest.mock import patch

import pyotp
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from kb.crypto import decrypt_value, is_encrypted_value
from kb.mfa import (
    MFA_SESSION_KEY,
    MFA_USER_SESSION_KEY,
    PENDING_MFA_RESET_CHALLENGE_ID_SESSION_KEY,
    PENDING_MFA_RESET_EXPIRES_AT_SESSION_KEY,
    PENDING_MFA_RESET_SECRET_SESSION_KEY,
    PENDING_MFA_RESET_USER_ID_SESSION_KEY,
    get_or_create_mfa_device,
)
from kb.models import UserProfile


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class SelfServiceMFAResetTests(TestCase):
    def setUp(self):
        self.password = "Safe-test-password-123!"
        self.user = get_user_model().objects.create_user(
            username="mfa-reset-self-test",
            email="mfa-reset-self-test@example.invalid",
            password=self.password,
        )
        self.device = get_or_create_mfa_device(self.user)
        self.device.confirmed = True
        self.device.save(update_fields=["confirmed"])
        self._login_with_mfa(self.client)

    def _login_with_mfa(self, client, backend="kb.backends.EmailOrUsernameModelBackend"):
        client.force_login(self.user, backend=backend)
        session = client.session
        session[MFA_SESSION_KEY] = True
        session[MFA_USER_SESSION_KEY] = str(self.user.pk)
        session.save()

    def _current_code(self):
        self.device.refresh_from_db()
        return pyotp.TOTP(self.device.get_secret()).now()

    def _begin_reset(self, password=None, mfa_code=None):
        return self.client.post(
            reverse("reset_mfa"),
            {
                "current_password": password or self.password,
                "mfa_code": mfa_code or self._current_code(),
            },
        )

    def _pending_state(self):
        session = self.client.session
        return {
            "secret": decrypt_value(session.get(PENDING_MFA_RESET_SECRET_SESSION_KEY, "")),
            "challenge_id": session.get(PENDING_MFA_RESET_CHALLENGE_ID_SESSION_KEY),
            "user_id": session.get(PENDING_MFA_RESET_USER_ID_SESSION_KEY),
            "expires_at": session.get(PENDING_MFA_RESET_EXPIRES_AT_SESSION_KEY),
        }

    def _different_code(self, secret):
        current = pyotp.TOTP(secret).now()
        replacement = (int(current) + 1) % 1_000_000
        return f"{replacement:06d}"

    def test_profile_reset_button_opens_reverification_form(self):
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="resetMfaModal"')
        self.assertContains(response, 'name="current_password"')
        self.assertContains(response, 'name="mfa_code"')
        self.assertContains(response, "Your current MFA remains active until the new code is confirmed.")

    def test_ad_managed_profile_hides_local_password_management_ui(self):
        profile, _created = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.LDAP_USER
        profile.auth_source = UserProfile.AuthSource.AD
        profile.save(update_fields=["account_type", "auth_source", "updated_at"])
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])
        self.client.logout()
        self._login_with_mfa(self.client, backend="kb.backends.PlaceholderLDAPBackend")

        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["user_is_ldap_managed"])
        self.assertFalse(response.context["can_change_local_password"])
        self.assertNotContains(response, 'data-target="#changePasswordModal"')
        self.assertNotContains(response, 'id="changePasswordModal"')
        self.assertNotContains(response, 'name="old_password"')
        self.assertNotContains(response, "Password syncs with your company password.")

    def test_local_profile_keeps_password_management_ui(self):
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["user_is_ldap_managed"])
        self.assertTrue(response.context["can_change_local_password"])
        self.assertContains(response, 'data-target="#changePasswordModal"')
        self.assertContains(response, 'id="changePasswordModal"')
        self.assertContains(response, 'name="old_password"')

    def test_wrong_password_does_not_start_or_change_mfa(self):
        old_secret = self.device.get_secret()

        response = self._begin_reset(password="wrong-password")

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertNotIn(PENDING_MFA_RESET_SECRET_SESSION_KEY, self.client.session)

    def test_wrong_current_mfa_code_does_not_start_or_change_mfa(self):
        old_secret = self.device.get_secret()

        response = self._begin_reset(mfa_code=self._different_code(old_secret))

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertNotIn(PENDING_MFA_RESET_SECRET_SESSION_KEY, self.client.session)

    def test_reverification_only_stages_new_secret_and_keeps_current_mfa_active(self):
        other_client = Client()
        self._login_with_mfa(other_client)
        other_session_key = other_client.session.session_key
        old_secret = self.device.get_secret()

        response = self._begin_reset()

        self.assertRedirects(response, reverse("mfa_reset_setup"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertTrue(Session.objects.filter(session_key=other_session_key).exists())

        session = self.client.session
        self.assertIn("_auth_user_id", session)
        self.assertTrue(session.get(MFA_SESSION_KEY))
        self.assertEqual(session.get(MFA_USER_SESSION_KEY), str(self.user.pk))

        pending = self._pending_state()
        self.assertTrue(
            is_encrypted_value(
                self.client.session.get(PENDING_MFA_RESET_SECRET_SESSION_KEY, "")
            )
        )
        self.assertEqual(pending["user_id"], str(self.user.pk))
        self.assertTrue(pending["challenge_id"])
        self.assertTrue(pending["expires_at"])
        self.assertTrue(pending["secret"])
        self.assertNotEqual(pending["secret"], old_secret)

    def test_setup_page_displays_staged_qr_without_changing_current_device(self):
        old_secret = self.device.get_secret()
        self._begin_reset()

        response = self.client.get(reverse("mfa_reset_setup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="code"')
        self.assertContains(response, 'name="challenge_id"')
        self.assertContains(response, "Your current MFA remains active")
        self.assertTrue(response.context["manual_secret"])
        self.assertNotEqual(response.context["manual_secret"], old_secret)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)

    def test_wrong_new_code_keeps_current_mfa_unchanged(self):
        old_secret = self.device.get_secret()
        self._begin_reset()
        pending = self._pending_state()

        response = self.client.post(
            reverse("mfa_reset_setup"),
            {
                "challenge_id": pending["challenge_id"],
                "code": self._different_code(pending["secret"]),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid authenticator code")
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertIn(PENDING_MFA_RESET_SECRET_SESSION_KEY, self.client.session)

    def test_new_secret_replaces_current_mfa_only_after_new_code_is_verified(self):
        other_client = Client()
        self._login_with_mfa(other_client)
        other_session_key = other_client.session.session_key
        current_session_key = self.client.session.session_key
        old_secret = self.device.get_secret()

        self._begin_reset()
        pending = self._pending_state()
        new_code = pyotp.TOTP(pending["secret"]).now()

        response = self.client.post(
            reverse("mfa_reset_setup"),
            {
                "challenge_id": pending["challenge_id"],
                "code": new_code,
            },
        )

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertNotEqual(self.device.get_secret(), old_secret)
        self.assertEqual(self.device.get_secret(), pending["secret"])
        self.assertIsNotNone(self.device.reset_at)
        self.assertIsNotNone(self.device.confirmed_at)
        self.assertIsNotNone(self.device.last_verified_at)

        self.assertFalse(Session.objects.filter(session_key=other_session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=current_session_key).exists())
        session = self.client.session
        self.assertIn("_auth_user_id", session)
        self.assertTrue(session.get(MFA_SESSION_KEY))
        self.assertEqual(session.get(MFA_USER_SESSION_KEY), str(self.user.pk))
        self.assertNotIn(PENDING_MFA_RESET_SECRET_SESSION_KEY, session)
        self.assertNotIn(PENDING_MFA_RESET_CHALLENGE_ID_SESSION_KEY, session)

    def test_cancelling_staged_replacement_keeps_current_mfa(self):
        old_secret = self.device.get_secret()
        self._begin_reset()

        response = self.client.post(reverse("mfa_reset_cancel"))

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertNotIn(PENDING_MFA_RESET_SECRET_SESSION_KEY, self.client.session)

    def test_expired_staged_replacement_keeps_current_mfa(self):
        old_secret = self.device.get_secret()
        self._begin_reset()
        session = self.client.session
        session[PENDING_MFA_RESET_EXPIRES_AT_SESSION_KEY] = (
            timezone.now() - timezone.timedelta(seconds=1)
        ).isoformat()
        session.save()

        response = self.client.get(reverse("mfa_reset_setup"))

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertNotIn(PENDING_MFA_RESET_SECRET_SESSION_KEY, self.client.session)

    def test_staged_replacement_cannot_overwrite_device_changed_elsewhere(self):
        self._begin_reset()
        pending = self._pending_state()
        changed_secret = pyotp.random_base32()
        self.device.set_secret(changed_secret)
        self.device.save(update_fields=["secret"])

        response = self.client.post(
            reverse("mfa_reset_setup"),
            {
                "challenge_id": pending["challenge_id"],
                "code": pyotp.TOTP(pending["secret"]).now(),
            },
        )

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertEqual(self.device.get_secret(), changed_secret)
        self.assertNotIn(PENDING_MFA_RESET_SECRET_SESSION_KEY, self.client.session)

    def test_setup_route_without_staged_reset_returns_to_profile(self):
        response = self.client.get(reverse("mfa_reset_setup"))

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)

    def test_ad_managed_user_password_is_reverified_through_authentication_backend(self):
        profile, _created = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.LDAP_USER
        profile.auth_source = UserProfile.AuthSource.AD
        profile.save(update_fields=["account_type", "auth_source", "updated_at"])
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])

        self.client.logout()
        self._login_with_mfa(self.client, backend="kb.backends.PlaceholderLDAPBackend")
        old_secret = self.device.get_secret()

        with patch("kb.views.mfa.authenticate", return_value=self.user) as mocked_authenticate:
            response = self.client.post(
                reverse("reset_mfa"),
                {
                    "current_password": "current-ad-password",
                    "mfa_code": self._current_code(),
                },
            )

        self.assertRedirects(response, reverse("mfa_reset_setup"), fetch_redirect_response=False)
        mocked_authenticate.assert_called_once_with(
            request=None,
            username=self.user.get_username(),
            password="current-ad-password",
        )
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertTrue(self._pending_state()["secret"])
