from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from kb.models import ActivityLog, SuggestedArticle
from kb.views.services import record_article_session_view


class ArticleSessionViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="session-view-owner",
            email="session-view-owner@example.invalid",
            password="safe-test-password",
        )
        self.viewer = User.objects.create_user(
            username="session-view-user",
            email="session-view-user@example.invalid",
            password="safe-test-password",
        )
        self.article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Session view article",
            body="Published article body.",
            filename="session-view-article.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.factory = RequestFactory()

    def _request(self, user, *, method="GET", shared_session=None):
        request = (
            self.factory.get(f"/articles/{self.article.pk}/")
            if method == "GET"
            else self.factory.post(f"/articles/{self.article.pk}/")
        )
        request.user = user
        if shared_session is None:
            SessionMiddleware(lambda _request: None).process_request(request)
        else:
            request.session = shared_session
        return request

    def test_same_browser_session_counts_article_only_once(self):
        first_request = self._request(self.viewer)

        self.assertTrue(record_article_session_view(first_request, self.article))
        self.assertFalse(record_article_session_view(first_request, self.article))
        self.assertFalse(
            record_article_session_view(
                self._request(self.viewer, shared_session=first_request.session),
                self.article,
            )
        )

        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 1)
        self.assertFalse(
            ActivityLog.objects.filter(article_id=self.article.pk).exists()
        )

    def test_new_browser_or_login_session_can_add_another_view(self):
        first_browser = self._request(self.viewer)
        second_browser = self._request(self.viewer)

        self.assertTrue(record_article_session_view(first_browser, self.article))
        self.assertTrue(record_article_session_view(second_browser, self.article))
        self.assertFalse(record_article_session_view(second_browser, self.article))

        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 2)

    def test_each_article_is_tracked_separately_in_the_session(self):
        other_article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Second session view article",
            body="Another published article body.",
            filename="second-session-view-article.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        request = self._request(self.viewer)

        self.assertTrue(record_article_session_view(request, self.article))
        self.assertTrue(record_article_session_view(request, other_article))
        self.assertFalse(record_article_session_view(request, self.article))

        self.article.refresh_from_db()
        other_article.refresh_from_db()
        self.assertEqual(self.article.view_count, 1)
        self.assertEqual(other_article.view_count, 1)

    def test_anonymous_post_and_unpublished_previews_are_not_counted(self):
        self.assertFalse(
            record_article_session_view(
                self._request(AnonymousUser()),
                self.article,
            )
        )
        self.assertFalse(
            record_article_session_view(
                self._request(self.viewer, method="POST"),
                self.article,
            )
        )

        self.article.status = SuggestedArticle.Status.DRAFT
        self.article.save(update_fields=["status"])
        self.assertFalse(
            record_article_session_view(self._request(self.viewer), self.article)
        )

        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 0)
        self.assertFalse(
            ActivityLog.objects.filter(article_id=self.article.pk).exists()
        )

    def test_missing_session_is_a_safe_no_op(self):
        request = self.factory.get(f"/articles/{self.article.pk}/")
        request.user = self.viewer

        self.assertFalse(record_article_session_view(request, self.article))
        self.article.refresh_from_db()
        self.assertEqual(self.article.view_count, 0)
