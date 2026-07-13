"""Regression tests for direct, role-scoped SMTP review notifications."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings

from kb.models import SuggestedArticle
from kb.notifications import (
    NOTIFICATION_KIND_NEW_SUBMISSION,
    OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED,
    OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED,
    OWNER_NOTIFICATION_KIND_UPDATE_APPROVED,
    OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED,
    _owner_notification_subject_and_body,
    deliver_article_review_notification,
    get_article_review_recipients,
    send_article_review_notification_after_commit,
)
from kb.permissions import (
    ROLE_ADMIN_USERS,
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_DISABLED_USER,
    ROLE_INTERNAL_ARTICLE_APPROVER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
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
class ArticleReviewNotificationTests(TestCase):
    """Public and internal recipient pools remain strictly separated."""

    def setUp(self):
        seed_djopenkb_role_groups()
        User = get_user_model()

        self.author = User.objects.create_user(
            username="notification-author",
            email="notification-author@example.invalid",
            password="safe-test-password",
        )
        self.public_approver = User.objects.create_user(
            username="notification-public-approver",
            email="public-approver@example.invalid",
            password="safe-test-password",
        )
        self.public_manager = User.objects.create_user(
            username="notification-public-manager",
            email="public-manager@example.invalid",
            password="safe-test-password",
        )
        self.internal_approver = User.objects.create_user(
            username="notification-internal-approver",
            email="internal-approver@example.invalid",
            password="safe-test-password",
        )
        self.internal_manager = User.objects.create_user(
            username="notification-internal-manager",
            email="internal-manager@example.invalid",
            password="safe-test-password",
        )
        self.admin = User.objects.create_user(
            username="notification-admin",
            email="admin@example.invalid",
            password="safe-test-password",
        )
        self.disabled_public_approver = User.objects.create_user(
            username="notification-disabled-reviewer",
            email="disabled-reviewer@example.invalid",
            password="safe-test-password",
        )

        assign_single_role_group(self.public_approver, ROLE_ARTICLE_APPROVER)
        assign_single_role_group(self.public_manager, ROLE_ARTICLE_MANAGER)
        assign_single_role_group(self.internal_approver, ROLE_INTERNAL_ARTICLE_APPROVER)
        assign_single_role_group(self.internal_manager, ROLE_INTERNAL_ARTICLE_MANAGER)
        assign_single_role_group(self.admin, ROLE_ADMIN_USERS)
        assign_single_role_group(self.disabled_public_approver, ROLE_ARTICLE_APPROVER)
        assign_single_role_group(self.disabled_public_approver, ROLE_DISABLED_USER)

    def _article(self, *, visibility=SuggestedArticle.Visibility.PUBLIC, **overrides):
        values = {
            "owner": self.author,
            "title": "Pending notification article",
            "body": "This is test content that must not be emailed for internal items.",
            "visibility": visibility,
            "status": SuggestedArticle.Status.PENDING,
        }
        values.update(overrides)
        return SuggestedArticle.objects.create(**values)

    def _assert_single_bcc_message(self, expected_recipients):
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [])
        self.assertEqual(message.cc, [])
        self.assertSetEqual(set(message.bcc), set(expected_recipients))
        self.assertNotIn("Bcc", message.message().as_string())

    def test_public_submission_sends_one_bcc_message_to_public_reviewers_and_admins(self):
        article = self._article()
        response = deliver_article_review_notification(
            article.pk,
            NOTIFICATION_KIND_NEW_SUBMISSION,
            self.author.pk,
        )

        self.assertEqual(response["status"], "sent")
        self.assertEqual(response["recipient_count"], 3)
        self.assertEqual(response["relay_accepted_count"], 3)
        self._assert_single_bcc_message(
            {
                self.public_approver.email,
                self.public_manager.email,
                self.admin.email,
            }
        )
        self.assertNotIn(self.internal_approver.email, mail.outbox[0].bcc)
        self.assertNotIn(self.disabled_public_approver.email, mail.outbox[0].bcc)

    def test_internal_submission_sends_one_bcc_message_to_internal_reviewers_and_admins(self):
        secret_internal_title = "Private identity migration plan"
        article = self._article(
            visibility=SuggestedArticle.Visibility.INTERNAL,
            title=secret_internal_title,
        )

        response = deliver_article_review_notification(
            article.pk,
            NOTIFICATION_KIND_NEW_SUBMISSION,
            self.author.pk,
        )

        self.assertEqual(response["status"], "sent")
        self._assert_single_bcc_message(
            {
                self.internal_approver.email,
                self.internal_manager.email,
                self.admin.email,
            }
        )
        rendered_mail = f"{mail.outbox[0].subject}\n{mail.outbox[0].body}"
        self.assertNotIn(secret_internal_title, rendered_mail)
        self.assertNotIn("This is test content", rendered_mail)
        self.assertIn("/internal/profile/admin/pending-articles/", rendered_mail)

    def test_direct_superuser_without_admin_users_role_is_not_a_reviewer_recipient(self):
        User = get_user_model()
        legacy_superuser = User.objects.create_superuser(
            username="notification-legacy-superuser",
            email="legacy-superuser@example.invalid",
            password="safe-test-password",
        )
        legacy_superuser.groups.clear()

        article = self._article()
        recipients = get_article_review_recipients(article)
        addresses = [recipient.email for recipient in recipients]

        self.assertNotIn(legacy_superuser.email, addresses)
        self.assertEqual(addresses.count(self.admin.email), 1)

    def test_resolved_article_does_not_send_a_late_notification(self):
        article = self._article(status=SuggestedArticle.Status.PUBLISHED)

        response = deliver_article_review_notification(
            article.pk,
            NOTIFICATION_KIND_NEW_SUBMISSION,
            self.author.pk,
        )

        self.assertEqual(response["status"], "no_longer_pending")
        self.assertEqual(mail.outbox, [])

    def test_submission_schedules_direct_delivery_after_commit_without_a_celery_task(self):
        article = self._article()
        request = RequestFactory().post("/suggest/")
        request.user = self.author

        with patch("kb.notifications.deliver_article_review_notification") as deliver, patch(
            "kb.notifications.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ) as on_commit:
            scheduled = send_article_review_notification_after_commit(
                request,
                article,
                NOTIFICATION_KIND_NEW_SUBMISSION,
            )

        self.assertTrue(scheduled)
        self.assertEqual(on_commit.call_count, 1)
        deliver.assert_called_once_with(
            article.pk,
            NOTIFICATION_KIND_NEW_SUBMISSION,
            self.author.pk,
        )
        self.assertEqual(mail.outbox, [])

    def test_relay_failure_is_recorded_without_raising_into_the_article_workflow(self):
        article = self._article()

        with patch("kb.notifications._send_bcc_message", side_effect=OSError("relay unavailable")):
            response = deliver_article_review_notification(
                article.pk,
                NOTIFICATION_KIND_NEW_SUBMISSION,
                self.author.pk,
            )

        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["recipient_count"], 3)
        self.assertEqual(response["relay_accepted_count"], 0)
        self.assertEqual(mail.outbox, [])


@override_settings(
    SITE_BASE_URL="https://knowledge.example.invalid",
    EMAIL_SUBJECT_PREFIX="[Knowledge Repository] ",
)
class ArticleOwnerNotificationWordingTests(TestCase):
    """Public owner emails stay generic while Internal scope remains explicit."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="owner-wording-test",
            email="owner-wording@example.invalid",
            password="safe-test-password",
        )

    def _article(self, *, internal: bool) -> SuggestedArticle:
        return SuggestedArticle.objects.create(
            owner=self.owner,
            title="Owner notification wording article",
            body="Test body",
            visibility=(
                SuggestedArticle.Visibility.INTERNAL
                if internal
                else SuggestedArticle.Visibility.PUBLIC
            ),
            status=SuggestedArticle.Status.PUBLISHED,
        )

    def test_public_owner_notifications_do_not_use_public_article_wording(self):
        article = self._article(internal=False)
        expectations = {
            OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED: (
                "[Knowledge Repository] Your article is approved",
                "Your article has been approved and is now published.",
            ),
            OWNER_NOTIFICATION_KIND_UPDATE_APPROVED: (
                "[Knowledge Repository] Your article update is approved",
                "Your submitted article update has been approved and is now published.",
            ),
            OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED: (
                "[Knowledge Repository] Your article needs changes",
                "Your article was marked as Pending failed and has not been published.",
            ),
            OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED: (
                "[Knowledge Repository] Your article update needs changes",
                "Your submitted article update was marked as Pending failed.",
            ),
        }

        for notification_kind, (expected_subject, expected_body_line) in expectations.items():
            with self.subTest(notification_kind=notification_kind):
                subject, body = _owner_notification_subject_and_body(
                    article,
                    notification_kind,
                )
                self.assertEqual(subject, expected_subject)
                self.assertIn(expected_body_line, body)
                self.assertNotIn("public article", f"{subject}\n{body}".lower())

    def test_internal_owner_notifications_keep_internal_article_wording(self):
        article = self._article(internal=True)
        expectations = {
            OWNER_NOTIFICATION_KIND_ARTICLE_APPROVED: (
                "[Knowledge Repository] Your internal article is approved",
                "Your internal article has been approved and is now published.",
            ),
            OWNER_NOTIFICATION_KIND_UPDATE_APPROVED: (
                "[Knowledge Repository] Your internal article update is approved",
                "Your submitted internal article update has been approved and is now published.",
            ),
            OWNER_NOTIFICATION_KIND_ARTICLE_PENDING_FAILED: (
                "[Knowledge Repository] Your internal article needs changes",
                "Your internal article was marked as Pending failed and has not been published.",
            ),
            OWNER_NOTIFICATION_KIND_UPDATE_PENDING_FAILED: (
                "[Knowledge Repository] Your internal article update needs changes",
                "Your submitted internal article update was marked as Pending failed.",
            ),
        }

        for notification_kind, (expected_subject, expected_body_line) in expectations.items():
            with self.subTest(notification_kind=notification_kind):
                subject, body = _owner_notification_subject_and_body(
                    article,
                    notification_kind,
                )
                self.assertEqual(subject, expected_subject)
                self.assertIn(expected_body_line, body)
                self.assertIn("internal article", f"{subject}\n{body}".lower())
