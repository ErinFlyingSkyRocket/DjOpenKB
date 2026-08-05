"""Regression tests for SMTP alerts raised by new authentication lockouts."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from kb.auth_monitoring import record_auth_failure
from kb.models import AuthActivityLog, AuthLockoutPolicyStage, SiteSetting
from kb.notifications import get_auth_lockout_admin_recipients
from kb.permissions import (
    ROLE_ADMIN_USERS,
    ROLE_DISABLED_USER,
    assign_single_role_group,
    seed_djopenkb_role_groups,
)


@override_settings(
    EMAIL_NOTIFICATIONS_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="knowledge-repository@example.invalid",
    SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS=("example.invalid",),
    SITE_BASE_URL="https://knowledge.example.invalid",
    EMAIL_SUBJECT_PREFIX="[Knowledge Repository] ",
)
class AuthenticationLockoutNotificationTests(TestCase):
    """Only actual new known-account lockouts notify valid Admin Users."""

    def setUp(self):
        cache.clear()
        seed_djopenkb_role_groups()
        User = get_user_model()
        self.factory = RequestFactory()

        self.subject_user = User.objects.create_user(
            username="lockout-subject",
            email="lockout-subject@example.invalid",
            password="safe-test-password",
        )
        self.admin = User.objects.create_user(
            username="lockout-admin",
            email="lockout-admin@example.invalid",
            password="safe-test-password",
        )
        self.disabled_admin = User.objects.create_user(
            username="lockout-disabled-admin",
            email="lockout-disabled-admin@example.invalid",
            password="safe-test-password",
        )
        self.inactive_admin = User.objects.create_user(
            username="lockout-inactive-admin",
            email="lockout-inactive-admin@example.invalid",
            password="safe-test-password",
            is_active=False,
        )
        self.legacy_superuser = User.objects.create_superuser(
            username="lockout-legacy-superuser",
            email="lockout-legacy-superuser@example.invalid",
            password="safe-test-password",
        )
        self.legacy_superuser.groups.clear()

        assign_single_role_group(self.admin, ROLE_ADMIN_USERS)
        assign_single_role_group(self.disabled_admin, ROLE_ADMIN_USERS)
        assign_single_role_group(self.disabled_admin, ROLE_DISABLED_USER)
        assign_single_role_group(self.inactive_admin, ROLE_ADMIN_USERS)

        setting = SiteSetting.load()
        setting.auth_lockout_stages.all().delete()
        AuthLockoutPolicyStage.objects.create(
            site_setting=setting,
            sort_order=10,
            failure_limit=1,
            block_seconds=300,
            repeat_count=0,
            enabled=True,
        )

    def _request(self):
        return self.factory.post(
            "/login/",
            {"username": self.subject_user.username},
            REMOTE_ADDR="192.0.2.24",
            HTTP_USER_AGENT="lockout-notification-test-agent",
        )

    def test_only_current_active_admin_users_receive_one_bcc_alert_for_new_lockout(self):
        with patch(
            "kb.notifications.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ):
            result = record_auth_failure(
                self._request(),
                username=self.subject_user.username,
                purpose="password",
            )

        self.assertTrue(result["locked"])
        self.assertTrue(result["lockout_created"])
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [])
        self.assertEqual(message.cc, [])
        self.assertEqual(message.bcc, [self.admin.email])
        self.assertNotIn("Bcc", message.message().as_string())
        self.assertIn("Account: lockout-subject", message.body)
        self.assertIn("Lockout type: password sign-in", message.body)
        self.assertIn("Temporary lockout: 5 minutes", message.body)
        self.assertIn("Source IP: 192.0.2.24", message.body)
        self.assertIn("/admin/kb/authactivitylog/", message.body)

        recipients = [recipient.email for recipient in get_auth_lockout_admin_recipients()]
        self.assertEqual(recipients, [self.admin.email])
        self.assertNotIn(self.disabled_admin.email, recipients)
        self.assertNotIn(self.inactive_admin.email, recipients)
        self.assertNotIn(self.legacy_superuser.email, recipients)

    def test_retry_during_the_same_temporary_block_does_not_send_another_alert(self):
        with patch(
            "kb.notifications.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ):
            first = record_auth_failure(
                self._request(),
                username=self.subject_user.username,
                purpose="password",
            )
            retry = record_auth_failure(
                self._request(),
                username=self.subject_user.username,
                purpose="password",
            )

        self.assertTrue(first["lockout_created"])
        self.assertTrue(retry["locked"])
        self.assertFalse(retry["lockout_created"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            AuthActivityLog.objects.filter(
                event_type=AuthActivityLog.EventType.AUTH_LOCKOUT_TRIGGERED,
                user=self.subject_user,
            ).count(),
            1,
        )

    def test_unknown_username_lockout_is_logged_but_does_not_email_administrators(self):
        request = self.factory.post(
            "/login/",
            {"username": "unknown-lockout-subject"},
            REMOTE_ADDR="192.0.2.25",
        )
        with patch(
            "kb.notifications.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ):
            result = record_auth_failure(
                request,
                username="unknown-lockout-subject",
                purpose="password",
            )

        self.assertTrue(result["lockout_created"])
        event = AuthActivityLog.objects.get(
            event_type=AuthActivityLog.EventType.AUTH_LOCKOUT_TRIGGERED,
            username="unknown-lockout-subject",
        )
        self.assertIsNone(event.user)
        self.assertEqual(mail.outbox, [])
