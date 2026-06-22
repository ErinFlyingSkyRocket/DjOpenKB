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
