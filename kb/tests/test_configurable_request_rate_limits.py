from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from kb.middleware import ConfigurableRequestRateLimitMiddleware


class ConfigurableRequestRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.middleware = ConfigurableRequestRateLimitMiddleware(
            lambda request: HttpResponse("ok")
        )

    @staticmethod
    def _limits(**overrides):
        values = {"login": 8, "mfa": 10, "admin": 120}
        values.update(overrides)
        return values

    def _request(self, method, path, *, ip="192.0.2.10", user=None):
        request = getattr(self.factory, method.lower())(path)
        request.META["REMOTE_ADDR"] = ip
        request.user = user if user is not None else AnonymousUser()
        return request

    def test_login_post_limit_is_per_ip(self):
        with patch(
            "kb.middleware._configured_request_rate_limits",
            return_value=self._limits(login=2),
        ):
            self.assertEqual(self.middleware(self._request("POST", "/")).status_code, 200)
            self.assertEqual(self.middleware(self._request("POST", "/")).status_code, 200)
            response = self.middleware(self._request("POST", "/"))

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_login_get_requests_are_not_counted(self):
        with patch(
            "kb.middleware._configured_request_rate_limits",
            return_value=self._limits(login=1),
        ):
            for _index in range(5):
                self.assertEqual(self.middleware(self._request("GET", "/")).status_code, 200)
            self.assertEqual(self.middleware(self._request("POST", "/")).status_code, 200)

    def test_admin_limit_is_per_signed_in_administrator(self):
        User = get_user_model()
        first = User.objects.create_superuser(
            username="admin-one",
            email="admin.one@example.com",
            password="TestPassword123!",
        )
        second = User.objects.create_superuser(
            username="admin-two",
            email="admin.two@example.com",
            password="TestPassword123!",
        )

        with patch(
            "kb.middleware._configured_request_rate_limits",
            return_value=self._limits(admin=1),
        ):
            self.assertEqual(
                self.middleware(self._request("POST", "/admin/auth/user/", user=first)).status_code,
                200,
            )
            self.assertEqual(
                self.middleware(self._request("POST", "/admin/auth/user/", user=first)).status_code,
                429,
            )
            self.assertEqual(
                self.middleware(self._request("POST", "/admin/auth/user/", user=second)).status_code,
                200,
            )

    def test_zero_disables_one_application_side_limit(self):
        with patch(
            "kb.middleware._configured_request_rate_limits",
            return_value=self._limits(login=0),
        ):
            for _index in range(20):
                self.assertEqual(self.middleware(self._request("POST", "/")).status_code, 200)
