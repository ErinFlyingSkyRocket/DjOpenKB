from pathlib import Path

from django.test import SimpleTestCase


class NginxAdminAndBulkLimitTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = (
            Path(__file__).resolve().parents[2] / "nginx" / "nginx.conf"
        ).read_text(encoding="utf-8")

    def test_general_admin_uses_separate_edge_rate_bucket(self):
        self.assertIn("zone=djopenkb_admin_per_ip", self.config)
        self.assertIn("rate=180r/m", self.config)
        self.assertIn("location ^~ /admin/", self.config)
        self.assertIn("limit_req zone=djopenkb_admin_per_ip burst=30", self.config)

    def test_admin_mfa_endpoints_keep_strict_mfa_bucket(self):
        self.assertIn("location = /admin/mfa/start/", self.config)
        self.assertIn("location = /admin/mfa/verify/", self.config)
        self.assertGreaterEqual(
            self.config.count("limit_req zone=djopenkb_mfa_per_ip burst=5"),
            2,
        )

    def test_bulk_import_allows_multipart_overhead_and_streaming(self):
        self.assertIn("client_max_body_size 110m", self.config)
        self.assertIn("proxy_request_buffering off", self.config)
        self.assertIn("client_body_timeout 300s", self.config)
        self.assertIn("limit_conn djopenkb_conn_per_ip 1", self.config)
        self.assertIn("proxy_read_timeout 310s", self.config)
