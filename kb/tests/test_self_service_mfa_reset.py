from unittest.mock import patch

import pyotp
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from kb.mfa import (
    MFA_SESSION_KEY,
    MFA_USER_SESSION_KEY,
    PRE_MFA_BACKEND_SESSION_KEY,
    PRE_MFA_USER_ID_SESSION_KEY,
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

    def test_profile_reset_button_opens_reverification_form(self):
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="resetMfaModal"')
        self.assertContains(response, 'name="current_password"')
        self.assertContains(response, 'name="mfa_code"')

    def test_wrong_password_does_not_reset_mfa(self):
        old_secret = self.device.get_secret()

        response = self.client.post(
            reverse("reset_mfa"),
            {
                "current_password": "wrong-password",
                "mfa_code": self._current_code(),
            },
        )

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_current_mfa_code_does_not_reset_mfa(self):
        old_secret = self.device.get_secret()

        response = self.client.post(
            reverse("reset_mfa"),
            {
                "current_password": self.password,
                "mfa_code": "000000",
            },
        )

        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)
        self.assertEqual(self.device.get_secret(), old_secret)
        self.assertIn("_auth_user_id", self.client.session)

    def test_successful_reset_requires_both_factors_and_invalidates_other_sessions(self):
        other_client = Client()
        self._login_with_mfa(other_client)
        other_session_key = other_client.session.session_key
        self.assertTrue(Session.objects.filter(session_key=other_session_key).exists())

        old_secret = self.device.get_secret()
        response = self.client.post(
            reverse("reset_mfa"),
            {
                "current_password": self.password,
                "mfa_code": self._current_code(),
            },
        )

        self.assertRedirects(response, reverse("mfa_setup"), fetch_redirect_response=False)
        self.device.refresh_from_db()
        self.assertFalse(self.device.confirmed)
        self.assertNotEqual(self.device.get_secret(), old_secret)
        self.assertFalse(Session.objects.filter(session_key=other_session_key).exists())

        session = self.client.session
        self.assertNotIn("_auth_user_id", session)
        self.assertEqual(session.get(PRE_MFA_USER_ID_SESSION_KEY), str(self.user.pk))
        self.assertEqual(
            session.get(PRE_MFA_BACKEND_SESSION_KEY),
            "kb.backends.EmailOrUsernameModelBackend",
        )

    def test_ad_managed_user_password_is_reverified_through_authentication_backend(self):
        profile, _created = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.LDAP_USER
        profile.auth_source = UserProfile.AuthSource.AD
        profile.save(update_fields=["account_type", "auth_source", "updated_at"])
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])

        self.client.logout()
        self._login_with_mfa(self.client, backend="kb.backends.PlaceholderLDAPBackend")

        with patch("kb.views.mfa.authenticate", return_value=self.user) as mocked_authenticate:
            response = self.client.post(
                reverse("reset_mfa"),
                {
                    "current_password": "current-ad-password",
                    "mfa_code": self._current_code(),
                },
            )

        self.assertRedirects(response, reverse("mfa_setup"), fetch_redirect_response=False)
        mocked_authenticate.assert_called_once_with(
            request=None,
            username=self.user.get_username(),
            password="current-ad-password",
        )
        self.device.refresh_from_db()
        self.assertFalse(self.device.confirmed)
