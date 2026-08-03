"""Regression coverage for disabled verification fields during auth cooldowns."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from kb.auth_monitoring import (
    AUTH_LOCKOUT_COUNTDOWN_MARKER,
    build_auth_lockout_ui_context,
    format_auth_lockout_countdown,
    record_auth_failure,
)
from kb.mfa import (
    MFA_SESSION_KEY,
    MFA_USER_SESSION_KEY,
    get_or_create_mfa_device,
)
from kb.models import AuthLockoutPolicyStage, SiteSetting, SuggestedArticle


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class AuthenticationLockoutCountdownUITests(TestCase):
    def setUp(self):
        cache.clear()
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

        self.user = get_user_model().objects.create_user(
            username="lockout-ui-user",
            email="lockout-ui-user@example.invalid",
            password="Safe-test-password-123!",
        )
        self.device = get_or_create_mfa_device(self.user)
        self.device.confirmed = True
        self.device.save(update_fields=["confirmed"])

    def _force_fully_verified_login(self, client, user):
        client.force_login(user)
        session = client.session
        session[MFA_SESSION_KEY] = True
        session[MFA_USER_SESSION_KEY] = str(user.pk)
        session.save()

    def test_compact_countdown_format_and_existing_translation_template(self):
        self.assertEqual(format_auth_lockout_countdown(299), "4m 59s")
        self.assertEqual(format_auth_lockout_countdown(58), "58s")

        context = build_auth_lockout_ui_context(
            locked=True,
            retry_after_seconds=299,
            message="Too many incorrect MFA codes. Please try again in %(duration)s.",
            prefix="test_lockout",
        )
        self.assertTrue(context["test_lockout_active"])
        self.assertIn(AUTH_LOCKOUT_COUNTDOWN_MARKER, context["test_lockout_message_template"])
        self.assertIn("4m 59s", context["test_lockout_initial_message"])

    def test_login_lockout_disables_username_password_and_submit(self):
        client = Client()
        response = client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "incorrect-password",
                "login_mode": "local",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["login_lockout_active"])
        rendered = response.content.decode("utf-8")
        self.assertIn('id="id_username"', rendered)
        self.assertIn('id="id_password"', rendered)
        self.assertIn('id="login-submit-button"', rendered)
        self.assertGreaterEqual(rendered.count('disabled aria-disabled="true"'), 3)
        self.assertIn("auth-lockout-countdown.js", rendered)
        self.assertIn("m ", rendered)

    def test_locked_login_post_does_not_bind_or_recheck_credentials(self):
        client = Client()
        record_auth_failure(user=self.user, purpose="password")

        response = client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "Safe-test-password-123!",
                "login_mode": "local",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["login_lockout_active"])
        self.assertFalse(response.context["form"].is_bound)
        self.assertEqual(
            response.context["form"].initial.get("username"),
            self.user.username,
        )
        self.assertNotIn("_auth_user_id", client.session)

    def test_normal_mfa_lockout_disables_code_and_verify_button(self):
        client = Client()
        client.force_login(self.user)
        record_auth_failure(user=self.user, purpose="mfa")

        response = client.get(reverse("mfa_verify"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["mfa_rate_limit_active"])
        rendered = response.content.decode("utf-8")
        self.assertIn('id="id_code"', rendered)
        self.assertGreaterEqual(rendered.count('disabled aria-disabled="true"'), 2)
        self.assertIn("auth-lockout-countdown.js", rendered)

    def test_admin_mfa_lockout_disables_code_and_restart_actions(self):
        admin_user = get_user_model().objects.create_superuser(
            username="lockout-ui-admin",
            email="lockout-ui-admin@example.invalid",
            password="Safe-test-password-123!",
        )
        admin_device = get_or_create_mfa_device(admin_user)
        admin_device.confirmed = True
        admin_device.save(update_fields=["confirmed"])

        client = Client()
        client.force_login(admin_user)
        record_auth_failure(user=admin_user, purpose="admin_mfa")

        response = client.get(
            f"{reverse('admin_mfa_verify')}?next=/admin/&fresh=1",
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["admin_mfa_rate_limit_active"])
        rendered = response.content.decode("utf-8")
        self.assertIn('id="id_code"', rendered)
        self.assertGreaterEqual(rendered.count('disabled aria-disabled="true"'), 2)
        self.assertIn("auth-lockout-countdown.js", rendered)

    def test_profile_sensitive_confirmation_fields_show_both_cooldowns(self):
        client = Client()
        self._force_fully_verified_login(client, self.user)
        record_auth_failure(user=self.user, purpose="password")
        record_auth_failure(user=self.user, purpose="mfa")

        response = client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["profile_password_lockout_active"])
        self.assertTrue(response.context["profile_mfa_lockout_active"])
        rendered = response.content.decode("utf-8")
        self.assertIn('name="current_password"', rendered)
        self.assertIn('name="old_password"', rendered)
        self.assertIn('name="mfa_code"', rendered)
        self.assertIn("auth-lockout-countdown.js", rendered)
        self.assertGreaterEqual(rendered.count('disabled aria-disabled="true"'), 6)

    def test_article_delete_mfa_field_is_disabled_during_mfa_cooldown(self):
        admin_user = get_user_model().objects.create_superuser(
            username="lockout-delete-admin",
            email="lockout-delete-admin@example.invalid",
            password="Safe-test-password-123!",
        )
        admin_device = get_or_create_mfa_device(admin_user)
        admin_device.confirmed = True
        admin_device.save(update_fields=["confirmed"])
        article = SuggestedArticle.objects.create(
            owner=admin_user,
            title="Published lockout test article",
            body="Test body",
            status=SuggestedArticle.Status.PUBLISHED,
        )

        client = Client()
        self._force_fully_verified_login(client, admin_user)
        record_auth_failure(user=admin_user, purpose="mfa")

        response = client.get(reverse("delete_suggestion", args=[article.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["article_delete_mfa_lockout_active"])
        rendered = response.content.decode("utf-8")
        self.assertIn('id="deleteMfaCode"', rendered)
        self.assertGreaterEqual(rendered.count('disabled aria-disabled="true"'), 2)
        self.assertIn("auth-lockout-countdown.js", rendered)
