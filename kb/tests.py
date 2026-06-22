from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from .admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from .middleware import ForceLoginAndAdminGuardMiddleware


@override_settings(ADMIN_MFA_IDLE_TIMEOUT_SECONDS=600)
class AdminStepUpRouteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_superuser(
            username="admin-step-up-test",
            email="admin-step-up-test@example.invalid",
            password="safe-test-password",
        )

    def _request(self, path):
        request = self.factory.get(path)
        SessionMiddleware(lambda req: HttpResponse("ok")).process_request(request)
        request.session.save()
        request.user = self.user
        return request

    def test_only_full_maintenance_routes_are_step_up_protected(self):
        self.assertTrue(is_admin_step_up_path(reverse("admin:index")))
        self.assertTrue(is_admin_step_up_path(reverse("export_articles_zip")))
        self.assertTrue(is_admin_step_up_path(reverse("manage_article_deletion_queue")))
        self.assertFalse(is_admin_step_up_path(reverse("manage_pending_articles")))
        self.assertFalse(is_admin_step_up_path(reverse("manage_internal_pending_articles")))

    def test_custom_admin_tool_requires_step_up_mfa(self):
        path = reverse("export_articles_zip")
        response = AdminMFASessionMiddleware(lambda request: HttpResponse("ok"))(self._request(path))

        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response.url)
        self.assertEqual(parsed.path, reverse("admin_mfa_verify"))
        self.assertEqual(parse_qs(parsed.query).get("next"), [path])

    def test_force_guard_also_blocks_custom_admin_tool_without_step_up_mfa(self):
        path = reverse("export_articles_zip")
        response = ForceLoginAndAdminGuardMiddleware(lambda request: HttpResponse("ok"))(self._request(path))

        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response.url)
        self.assertEqual(parsed.path, reverse("admin_mfa_verify"))
        self.assertEqual(parse_qs(parsed.query).get("next"), [path])


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

        from .admin_security import (
            ADMIN_MFA_LAST_ACTIVITY_AT_KEY,
            ADMIN_MFA_USER_ID_KEY,
            ADMIN_MFA_VERIFIED_AT_KEY,
            ADMIN_MFA_VERIFIED_KEY,
        )
        from .mfa import MFA_SESSION_KEY, MFA_USER_SESSION_KEY, get_or_create_mfa_device

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
