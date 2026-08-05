from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from kb.models import SuggestedArticle
from kb.permissions import (
    ROLE_ARTICLE_MANAGER,
    ROLE_ARTICLE_WRITER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
    ROLE_INTERNAL_ARTICLE_WRITER,
    assign_single_role_group,
    seed_djopenkb_role_groups,
)
from kb.views.services import user_can_change_article_visibility
from kb.views.suggestions import edit_suggestion


class ArticleVisibilityEditPermissionTests(TestCase):
    """Visibility changes require Admin or both manager role groups."""

    def setUp(self):
        seed_djopenkb_role_groups()
        self.factory = RequestFactory()
        user_model = get_user_model()

        self.owner = user_model.objects.create_user(
            username="visibility-owner",
            email="visibility-owner@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.owner, ROLE_ARTICLE_WRITER)

        self.public_manager = user_model.objects.create_user(
            username="visibility-public-manager",
            email="visibility-public-manager@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.public_manager, ROLE_ARTICLE_MANAGER)

        self.internal_manager = user_model.objects.create_user(
            username="visibility-internal-manager",
            email="visibility-internal-manager@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.internal_manager, ROLE_INTERNAL_ARTICLE_MANAGER)

        self.dual_manager = user_model.objects.create_user(
            username="visibility-dual-manager",
            email="visibility-dual-manager@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.dual_manager, ROLE_ARTICLE_MANAGER)
        assign_single_role_group(self.dual_manager, ROLE_INTERNAL_ARTICLE_MANAGER)

        self.dual_writer = user_model.objects.create_user(
            username="visibility-dual-writer",
            email="visibility-dual-writer@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.dual_writer, ROLE_ARTICLE_WRITER)
        assign_single_role_group(self.dual_writer, ROLE_INTERNAL_ARTICLE_WRITER)

        self.admin = user_model.objects.create_superuser(
            username="visibility-admin",
            email="visibility-admin@example.invalid",
            password="safe-test-password",
        )

        self.public_article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Published visibility permission article",
            body="Published article body used for visibility permission tests.",
            keywords="visibility, permission",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )

    @staticmethod
    def _original_edit_view():
        view = edit_suggestion
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__
        return view

    def _request(self, method, user, article, data=None):
        path = reverse("edit_suggestion", kwargs={"article_id": article.pk})
        if method == "post":
            request = self.factory.post(path, data=data or {})
        else:
            request = self.factory.get(path)
        request.user = user
        SessionMiddleware(lambda req: HttpResponse("ok")).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def test_only_admin_or_both_manager_roles_can_change_visibility(self):
        self.assertTrue(user_can_change_article_visibility(self.admin, self.public_article))
        self.assertTrue(user_can_change_article_visibility(self.dual_manager, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.public_manager, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.internal_manager, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.dual_writer, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.owner, self.public_article))

    def test_dual_manager_edit_form_contains_both_visibility_choices_for_published_article(self):
        request = self._request("get", self.dual_manager, self.public_article)

        with patch("kb.views.suggestions.render", return_value=HttpResponse("ok")) as render_mock:
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertTrue(context["can_change_article_visibility"])
        self.assertEqual(
            [choice["value"] for choice in context["visibility_choices"]],
            [SuggestedArticle.Visibility.PUBLIC, SuggestedArticle.Visibility.INTERNAL],
        )

    def test_single_scope_manager_edit_form_does_not_contain_visibility_selector(self):
        request = self._request("get", self.public_manager, self.public_article)

        with patch("kb.views.suggestions.render", return_value=HttpResponse("ok")) as render_mock:
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertFalse(context["can_change_article_visibility"])
        self.assertEqual(context["visibility_choices"], [])

    def test_forged_single_scope_manager_post_cannot_change_visibility(self):
        request = self._request(
            "post",
            self.public_manager,
            self.public_article,
            data={
                "frm_kb_title": self.public_article.title,
                "frm_kb_body": self.public_article.body,
                "frm_kb_keywords": self.public_article.keywords,
                "article_visibility": SuggestedArticle.Visibility.INTERNAL,
                "status": SuggestedArticle.Status.PUBLISHED,
                "submit_action": "save",
            },
        )

        with (
            patch("kb.views.suggestions.write_article_files"),
            patch("kb.views.suggestions.sync_article_image_assets"),
            patch("kb.views.suggestions.clear_committed_pending_uploads"),
        ):
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 302)
        self.public_article.refresh_from_db()
        self.assertEqual(self.public_article.visibility, SuggestedArticle.Visibility.PUBLIC)

    def test_dual_manager_can_change_published_article_visibility(self):
        request = self._request(
            "post",
            self.dual_manager,
            self.public_article,
            data={
                "frm_kb_title": self.public_article.title,
                "frm_kb_body": self.public_article.body,
                "frm_kb_keywords": self.public_article.keywords,
                "article_visibility": SuggestedArticle.Visibility.INTERNAL,
                "status": SuggestedArticle.Status.PUBLISHED,
                "submit_action": "save",
            },
        )

        with (
            patch("kb.views.suggestions.delete_article_markdown_files"),
            patch("kb.views.suggestions.write_article_files"),
            patch("kb.views.suggestions.sync_article_image_assets"),
            patch("kb.views.suggestions.clear_committed_pending_uploads"),
        ):
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 302)
        self.public_article.refresh_from_db()
        self.assertEqual(self.public_article.visibility, SuggestedArticle.Visibility.INTERNAL)
