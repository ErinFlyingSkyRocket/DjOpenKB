from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class RequestRateLimitSimplificationTests(SimpleTestCase):
    def test_duplicate_application_middleware_is_removed(self):
        self.assertNotIn(
            "kb.middleware.ConfigurableRequestRateLimitMiddleware",
            settings.MIDDLEWARE,
        )

    def test_nginx_edge_limits_remain_configured(self):
        nginx_config = (
            Path(__file__).resolve().parents[3]
            / "nginx"
            / "nginx.conf"
        ).read_text(encoding="utf-8")

        self.assertIn("limit_req_zone", nginx_config)
        self.assertIn("limit_req", nginx_config)

    def test_progressive_authentication_lockout_code_remains_available(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "auth_monitoring.py"
        ).read_text(encoding="utf-8")

        self.assertIn("record_auth_failure", source)
        self.assertIn("get_auth_lockout_status", source)
