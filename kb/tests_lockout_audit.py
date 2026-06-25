"""Regression tests for explicit temporary-lockout audit records."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from .auth_monitoring import _lockout_keys, record_auth_failure
from .models import AuthActivityLog, AuthLockoutPolicyStage, SiteSetting


class AuthenticationLockoutAuditTests(TestCase):
    """A lockout must create one readable AuthActivityLog event per block."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="lockout-audit-test",
            email="lockout-audit-test@example.invalid",
            password="safe-test-password",
        )

        setting = SiteSetting.load()
        setting.auth_lockout_stages.all().delete()
        AuthLockoutPolicyStage.objects.create(
            site_setting=setting,
            sort_order=10,
            failure_limit=1,
            block_seconds=300,
            repeat_count=1,
            enabled=True,
        )
        AuthLockoutPolicyStage.objects.create(
            site_setting=setting,
            sort_order=20,
            failure_limit=1,
            block_seconds=3600,
            repeat_count=0,
            enabled=True,
        )

    def _request(self):
        return self.factory.post("/admin-mfa/", HTTP_USER_AGENT="lockout-audit-test-agent")

    def test_each_new_lockout_creates_one_dedicated_log_with_stage_duration(self):
        first = record_auth_failure(
            self._request(),
            user=self.user,
            purpose="admin_mfa",
        )

        self.assertTrue(first["locked"])
        self.assertTrue(first["lockout_created"])
        event = AuthActivityLog.objects.get(
            event_type=AuthActivityLog.EventType.AUTH_LOCKOUT_TRIGGERED,
            user=self.user,
        )
        self.assertFalse(event.success)
        self.assertEqual(event.details["purpose"], "admin_mfa")
        self.assertTrue(event.details["admin_step_up"])
        self.assertEqual(event.details["policy_stage"], 1)
        self.assertEqual(event.details["block_seconds"], 300)
        self.assertEqual(event.details["failure_limit"], 1)
        self.assertEqual(event.details["lockout_strike"], 1)

        # Attempts made during the active block must not create duplicate
        # "lockout triggered" events.
        blocked_retry = record_auth_failure(
            self._request(),
            user=self.user,
            purpose="admin_mfa",
        )
        self.assertTrue(blocked_retry["locked"])
        self.assertFalse(blocked_retry["lockout_created"])
        self.assertEqual(
            AuthActivityLog.objects.filter(
                event_type=AuthActivityLog.EventType.AUTH_LOCKOUT_TRIGGERED,
                user=self.user,
            ).count(),
            1,
        )

        # Simulate expiry of the first temporary block while retaining the
        # escalation strike. The next lockout must record the 1-hour stage.
        cache.delete(_lockout_keys(first["identifier"])["block"])
        second = record_auth_failure(
            self._request(),
            user=self.user,
            purpose="admin_mfa",
        )
        self.assertTrue(second["locked"])
        self.assertTrue(second["lockout_created"])

        events = list(
            AuthActivityLog.objects.filter(
                event_type=AuthActivityLog.EventType.AUTH_LOCKOUT_TRIGGERED,
                user=self.user,
            ).order_by("created_at", "pk")
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].details["policy_stage"], 2)
        self.assertEqual(events[1].details["block_seconds"], 3600)
        self.assertEqual(events[1].details["lockout_strike"], 2)
