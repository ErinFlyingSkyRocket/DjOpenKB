from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from kb.models import SuggestedArticle


class UserAuthorSnapshotSignalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="snapshot-owner",
            email="snapshot-owner@example.invalid",
            password="Different-Strong-Password-123!",
        )
        self.article = SuggestedArticle.objects.create(
            owner=self.user,
            title="Snapshot signal article",
            body="Snapshot signal article body",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )

    def test_last_login_save_does_not_touch_owned_articles(self):
        self.user.last_login = timezone.now()

        with patch("kb.signals.SuggestedArticle.objects.filter") as article_filter:
            self.user.save(update_fields=["last_login"])

        article_filter.assert_not_called()

    def test_identity_change_refreshes_snapshot_for_owned_articles(self):
        self.user.first_name = "Updated"
        self.user.last_name = "Owner"
        self.user.email = "updated-owner@example.invalid"
        self.user.save(update_fields=["first_name", "last_name", "email"])

        self.article.refresh_from_db()
        self.assertEqual(self.article.author_name_snapshot, "Updated Owner")
        self.assertEqual(
            self.article.author_email_snapshot,
            "updated-owner@example.invalid",
        )
