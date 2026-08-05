from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from kb.admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from kb.middleware import ForceLoginAndAdminGuardMiddleware


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class UserAdminResetActionTests(TestCase):
    """Regression coverage for the per-user Admin MFA/lockout reset buttons."""

    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-reset-test",
            email="admin-reset-test@example.invalid",
            password="safe-test-password",
        )
        self.target_user = get_user_model().objects.create_user(
            username="target-reset-test",
            email="target-reset-test@example.invalid",
            password="safe-test-password",
        )

        from django.utils import timezone

        from kb.admin_security import (
            ADMIN_MFA_LAST_ACTIVITY_AT_KEY,
            ADMIN_MFA_USER_ID_KEY,
            ADMIN_MFA_VERIFIED_AT_KEY,
            ADMIN_MFA_VERIFIED_KEY,
        )
        from kb.mfa import MFA_SESSION_KEY, MFA_USER_SESSION_KEY, get_or_create_mfa_device

        self.target_device = get_or_create_mfa_device(self.target_user)
        self.target_device.confirmed = True
        self.target_device.save(update_fields=["confirmed"])

        self.client.force_login(self.admin_user)
        session = self.client.session
        now = int(timezone.now().timestamp())
        session[MFA_SESSION_KEY] = True
        session[MFA_USER_SESSION_KEY] = str(self.admin_user.pk)
        session[ADMIN_MFA_VERIFIED_KEY] = True
        session[ADMIN_MFA_USER_ID_KEY] = str(self.admin_user.pk)
        session[ADMIN_MFA_VERIFIED_AT_KEY] = now
        session[ADMIN_MFA_LAST_ACTIVITY_AT_KEY] = now
        session.save()

    def _admin_request_kwargs(self):
        return {"REMOTE_ADDR": "127.0.0.1"}

    def test_user_admin_mfa_reset_confirmation_and_submit_work(self):
        url = reverse("admin:kb_user_reset_mfa", args=[self.target_user.pk])

        confirmation = self.client.get(url, **self._admin_request_kwargs())
        self.assertEqual(confirmation.status_code, 200)

        response = self.client.post(url, **self._admin_request_kwargs())
        self.assertRedirects(
            response,
            reverse("admin:auth_user_change", args=[self.target_user.pk]),
            fetch_redirect_response=False,
        )

        self.target_device.refresh_from_db()
        self.assertFalse(self.target_device.confirmed)
        self.assertIsNotNone(self.target_device.reset_at)

    def test_user_admin_lockout_reset_confirmation_and_submit_work(self):
        url = reverse("admin:kb_user_reset_auth_lockout", args=[self.target_user.pk])

        confirmation = self.client.get(url, **self._admin_request_kwargs())
        self.assertEqual(confirmation.status_code, 200)

        response = self.client.post(url, **self._admin_request_kwargs())
        self.assertRedirects(
            response,
            reverse("admin:auth_user_change", args=[self.target_user.pk]),
            fetch_redirect_response=False,
        )
