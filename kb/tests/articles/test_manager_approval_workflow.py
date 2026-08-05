from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from kb.admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from kb.middleware import ForceLoginAndAdminGuardMiddleware


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class ArticleManagerApprovalWorkflowTests(TestCase):
    """Regression coverage for public manager article approval/edit transitions."""

    def setUp(self):
        from unittest.mock import patch

        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.http import HttpResponse

        from kb.permissions import ROLE_ARTICLE_MANAGER, assign_single_role_group, seed_djopenkb_role_groups

        self._patch = patch
        self._fallback_storage = FallbackStorage
        self._session_middleware = SessionMiddleware
        self._response_class = HttpResponse
        self.factory = RequestFactory()

        seed_djopenkb_role_groups()
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="article-manager-workflow-test",
            email="article-manager-workflow-test@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.manager, ROLE_ARTICLE_MANAGER)
        self.owner = User.objects.create_user(
            username="article-owner-workflow-test",
            email="article-owner-workflow-test@example.invalid",
            password="safe-test-password",
        )

    def _post_review(self, article, *, status, editor_mode="review"):
        from kb.views.suggestions import edit_suggestion

        request = self.factory.post(
            reverse("edit_suggestion", args=[article.pk]),
            data={
                "frm_kb_title": article.pending_update_title or article.title,
                "frm_kb_body": article.pending_update_body or article.body,
                "frm_kb_keywords": article.pending_update_keywords or article.keywords,
                "submit_action": "save",
                "status": status,
                "editor_mode": editor_mode,
                "next": reverse("edit_my_suggestions"),
            },
        )
        request.user = self.manager
        self._session_middleware(lambda req: self._response_class("ok")).process_request(request)
        request.session.save()
        setattr(request, "_messages", self._fallback_storage(request))

        with self._patch.multiple(
            "kb.views.suggestions",
            write_article_files=lambda article: None,
            sync_article_image_assets=lambda article, old_assets=None: None,
            clear_committed_pending_uploads=lambda request, assets: None,
        ):
            return edit_suggestion(request, article.pk)

    def test_manager_can_publish_pending_public_article(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Manager review pending article",
            body="A valid article body for manager approval.",
            filename="manager-review-pending.md",
            status=SuggestedArticle.Status.PENDING,
        )

        response = self._post_review(article, status=SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.approved_by_id, self.manager.pk)
        self.assertIsNotNone(article.approved_at)

    def test_manager_publish_of_failed_update_applies_and_clears_staged_state(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.manager,
            title="Manager failed update article",
            body="Previously approved article body.",
            filename="manager-failed-update.md",
            status=SuggestedArticle.Status.PUBLISHED,
            pending_update_title="Manager revised article",
            pending_update_body="The corrected update body is ready to publish.",
            pending_update_keywords="manager, revised",
            update_status=SuggestedArticle.UpdateStatus.FAILED,
            review_notes="Please revise the update before approval.",
        )

        # This deliberately uses the normal personal Edit route. A Manager may
        # resolve their own failed update without needing a special URL flag.
        response = self._post_review(article, status=SuggestedArticle.Status.PUBLISHED, editor_mode="edit")
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.title, "Manager revised article")
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.NONE)
        self.assertFalse(article.pending_update_body)
        self.assertFalse(article.review_notes)
        self.assertEqual(article.approved_by_id, self.manager.pk)

    def test_manager_publish_of_saved_update_draft_clears_staged_state(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.manager,
            title="Manager saved update article",
            body="Previously approved article body.",
            filename="manager-saved-update.md",
            status=SuggestedArticle.Status.PUBLISHED,
            pending_update_title="Manager saved revision",
            pending_update_body="The saved revision is ready to publish.",
            pending_update_keywords="manager, saved",
            update_status=SuggestedArticle.UpdateStatus.NONE,
        )

        response = self._post_review(article, status=SuggestedArticle.Status.PUBLISHED, editor_mode="edit")
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.title, "Manager saved revision")
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.NONE)
        self.assertFalse(article.pending_update_body)
        self.assertEqual(article.approved_by_id, self.manager.pk)

    def test_manager_reopens_failed_update_without_hiding_published_article(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Manager reopens failed update",
            body="Currently published article body.",
            filename="manager-reopen-update.md",
            status=SuggestedArticle.Status.PUBLISHED,
            pending_update_title="Updated title for review",
            pending_update_body="Updated body that should return to review.",
            pending_update_keywords="review",
            update_status=SuggestedArticle.UpdateStatus.FAILED,
            review_notes="Original rejection feedback.",
        )

        response = self._post_review(article, status=SuggestedArticle.Status.PENDING)
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.PENDING)
        self.assertEqual(article.pending_update_body, "Updated body that should return to review.")
        self.assertFalse(article.review_notes)
        self.assertTrue(article.review_notes_history)
