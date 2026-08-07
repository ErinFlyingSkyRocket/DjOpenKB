from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from kb.models import SuggestedArticle
from kb.views.admin import manage_orphan_articles


class OrphanArticleManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="orphan-admin",
            email="orphan-admin@example.invalid",
            password="safe-test-password",
        )
        self.active_owner = User.objects.create_user(
            username="active-owner",
            email="active-owner@example.invalid",
            password="safe-test-password",
        )
        self.inactive_owner = User.objects.create_user(
            username="inactive-owner",
            email="inactive-owner@example.invalid",
            password="safe-test-password",
            is_active=False,
        )
        self.factory = RequestFactory()

    def _article(self, title, owner):
        return SuggestedArticle.objects.create(
            owner=owner,
            title=title,
            body=f"Body for {title}",
            status=SuggestedArticle.Status.PUBLISHED,
            filename=f"{title.lower().replace(' ', '-')}.md",
        )

    def test_only_owner_null_articles_are_listed_as_orphans(self):
        true_orphan = self._article("True orphan", None)
        self._article("Inactive owner article", self.inactive_owner)
        self._article("Active owner article", self.active_owner)

        request = self.factory.get("/admin-tools/orphan-articles/")
        request.user = self.admin

        with patch("kb.views.admin.render", return_value=HttpResponse("ok")) as render_mock:
            # Call the undecorated view so this test focuses only on orphan
            # classification rather than session/admin-route middleware.
            response = manage_orphan_articles.__wrapped__(request)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        listed_ids = {article.pk for article in context["articles"]}
        self.assertEqual(listed_ids, {true_orphan.pk})
        self.assertEqual(context["total_orphan_article_count"], 1)
        self.assertEqual(context["orphan_result_count"], 1)
