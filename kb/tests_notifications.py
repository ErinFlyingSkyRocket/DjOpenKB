"""Regression tests for role-scoped SMTP review notifications."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from .models import SuggestedArticle
from .notifications import (
    NOTIFICATION_KIND_NEW_SUBMISSION,
    deliver_article_review_notification,
    get_article_review_recipients,
)
from .permissions import (
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
    """Public and internal reviewer pools must remain strictly separated."""

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

    def test_public_submission_notifies_only_public_reviewer_roles_and_admins(self):
        article = self._article()
        response = deliver_article_review_notification(
            article.pk,
            NOTIFICATION_KIND_NEW_SUBMISSION,
            self.author.pk,
        )

        self.assertEqual(response["status"], "sent")
        self.assertEqual(response["recipient_count"], 3)
        self.assertEqual(len(mail.outbox), 3)

        recipients = {message.to[0] for message in mail.outbox}
        self.assertSetEqual(
            recipients,
            {
                self.public_approver.email,
                self.public_manager.email,
                self.admin.email,
            },
        )
        self.assertTrue(
            all(message.to == [message.to[0]] for message in mail.outbox),
            "Each reviewer must receive a separate message.",
        )
        self.assertNotIn(self.internal_approver.email, recipients)
        self.assertNotIn(self.disabled_public_approver.email, recipients)

    def test_internal_submission_notifies_only_internal_reviewer_roles_and_admins(self):
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
        recipients = {message.to[0] for message in mail.outbox}
        self.assertSetEqual(
            recipients,
            {
                self.internal_approver.email,
                self.internal_manager.email,
                self.admin.email,
            },
        )
        rendered_mail = "\n".join(
            f"{message.subject}\n{message.body}" for message in mail.outbox
        )
        self.assertNotIn(secret_internal_title, rendered_mail)
        self.assertNotIn("This is test content", rendered_mail)
        self.assertIn("/internal/profile/admin/pending-articles/", rendered_mail)

    def test_direct_superuser_is_included_once_even_without_group(self):
        User = get_user_model()
        legacy_superuser = User.objects.create_superuser(
            username="notification-legacy-superuser",
            email="legacy-superuser@example.invalid",
            password="safe-test-password",
        )
        # Simulate an older account that has not yet been normalised into Admin Users.
        legacy_superuser.groups.clear()

        article = self._article()
        recipients = get_article_review_recipients(article)
        addresses = [recipient.email for recipient in recipients]

        self.assertIn(legacy_superuser.email, addresses)
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
