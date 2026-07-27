from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from kb.models import ActivityLog, ArticleDailyView, SuggestedArticle
from kb.views.services import record_article_daily_user_view


class ArticleDailyViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="daily-view-owner",
            email="daily-view-owner@example.invalid",
            password="safe-test-password",
        )
        self.viewer = User.objects.create_user(
            username="daily-view-user",
            email="daily-view-user@example.invalid",
            password="safe-test-password",
        )
        self.other_viewer = User.objects.create_user(
            username="daily-view-other",
            email="daily-view-other@example.invalid",
            password="safe-test-password",
        )
        self.article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Daily unique view article",
            body="Published article body.",
            filename="daily-unique-view-article.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.factory = RequestFactory()

    def _request(self, user, *, method="GET"):
        request = (
            self.factory.get(f"/articles/{self.article.pk}/")
            if method == "GET"
            else self.factory.post(f"/articles/{self.article.pk}/")
        )
        request.user = user
        return request

    @patch("kb.views.services.timezone.localdate", return_value=date(2026, 7, 27))
    def test_same_user_is_counted_only_once_on_same_day(self, _localdate):
        request = self._request(self.viewer)

        self.assertTrue(record_article_daily_user_view(request, self.article))
        self.assertFalse(record_article_daily_user_view(request, self.article))
        self.assertFalse(record_article_daily_user_view(self._request(self.viewer), self.article))

        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 1)
        self.assertEqual(ArticleDailyView.objects.count(), 1)
        self.assertEqual(
            ActivityLog.objects.filter(
                event_type=ActivityLog.EventType.ARTICLE_VIEWED,
                article_id=self.article.pk,
                user_id=self.viewer.pk,
            ).count(),
            1,
        )

    def test_same_user_can_add_one_new_view_on_later_day(self):
        with patch("kb.views.services.timezone.localdate", return_value=date(2026, 7, 27)):
            self.assertTrue(
                record_article_daily_user_view(self._request(self.viewer), self.article)
            )

        with patch("kb.views.services.timezone.localdate", return_value=date(2026, 7, 28)):
            self.assertTrue(
                record_article_daily_user_view(self._request(self.viewer), self.article)
            )
            self.assertFalse(
                record_article_daily_user_view(self._request(self.viewer), self.article)
            )

        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 2)
        self.assertEqual(ArticleDailyView.objects.count(), 2)

    @patch("kb.views.services.timezone.localdate", return_value=date(2026, 7, 27))
    def test_different_users_each_add_one_view_on_same_day(self, _localdate):
        self.assertTrue(
            record_article_daily_user_view(self._request(self.viewer), self.article)
        )
        self.assertTrue(
            record_article_daily_user_view(self._request(self.other_viewer), self.article)
        )

        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 2)
        self.assertEqual(ArticleDailyView.objects.count(), 2)

    @patch("kb.views.services.timezone.localdate", return_value=date(2026, 7, 27))
    def test_anonymous_post_and_unpublished_previews_are_not_counted(self, _localdate):
        self.assertFalse(
            record_article_daily_user_view(self._request(AnonymousUser()), self.article)
        )
        self.assertFalse(
            record_article_daily_user_view(
                self._request(self.viewer, method="POST"),
                self.article,
            )
        )

        self.article.status = SuggestedArticle.Status.DRAFT
        self.article.save(update_fields=["status"])
        self.assertFalse(
            record_article_daily_user_view(self._request(self.viewer), self.article)
        )

        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 0)
        self.assertFalse(ArticleDailyView.objects.exists())

    @patch("kb.views.services.timezone.localdate", return_value=date(2026, 7, 27))
    def test_historical_marker_remains_if_user_is_deleted(self, _localdate):
        viewer_id = self.viewer.pk
        self.assertTrue(
            record_article_daily_user_view(self._request(self.viewer), self.article)
        )

        self.viewer.delete()

        marker = ArticleDailyView.objects.get()
        self.assertEqual(marker.user_id, viewer_id)
        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 1)
