from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from kb.middleware import _admin_cidr_allowed, _configured_admin_networks


class AdminCidrFailClosedTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("kb.middleware.SiteSetting.load", side_effect=RuntimeError("database unavailable"))
    def test_configuration_read_failure_denies_admin_access(self, _load_mock):
        with self.assertLogs("kb.middleware", level="ERROR"):
            enabled, networks = _configured_admin_networks()

        self.assertTrue(enabled)
        self.assertEqual(networks, [])

        request = self.factory.get("/admin/", REMOTE_ADDR="127.0.0.1")
        with self.assertLogs("kb.middleware", level="ERROR"):
            self.assertFalse(_admin_cidr_allowed(request))

    @patch("kb.middleware.SiteSetting.load")
    def test_explicitly_disabled_allowlist_remains_open(self, load_mock):
        load_mock.return_value = SimpleNamespace(
            admin_ip_allowlist_enabled=False,
            admin_allowed_cidrs="",
        )

        request = self.factory.get("/admin/", REMOTE_ADDR="203.0.113.10")
        self.assertTrue(_admin_cidr_allowed(request))

    @patch("kb.middleware.SiteSetting.load")
    def test_enabled_valid_network_allows_only_matching_client(self, load_mock):
        load_mock.return_value = SimpleNamespace(
            admin_ip_allowlist_enabled=True,
            admin_allowed_cidrs="10.20.0.0/16",
        )

        allowed = self.factory.get("/admin/", REMOTE_ADDR="10.20.5.9")
        denied = self.factory.get("/admin/", REMOTE_ADDR="10.21.5.9")

        self.assertTrue(_admin_cidr_allowed(allowed))
        self.assertFalse(_admin_cidr_allowed(denied))
