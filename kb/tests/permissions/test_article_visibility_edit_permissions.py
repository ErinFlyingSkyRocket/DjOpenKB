from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from kb.models import SuggestedArticle
from kb.permissions import (
    ROLE_ARTICLE_APPROVER,
    ROLE_ARTICLE_MANAGER,
    ROLE_ARTICLE_WRITER,
    ROLE_INTERNAL_ARTICLE_APPROVER,
    ROLE_INTERNAL_ARTICLE_MANAGER,
    ROLE_INTERNAL_ARTICLE_WRITER,
    assign_single_role_group,
    seed_djopenkb_role_groups,
)
from kb.views.services import (
    choose_requested_article_visibility,
    user_can_change_article_visibility,
)
from kb.views.suggestions import _suggest_unified, edit_suggestion


class ArticleVisibilityEditPermissionTests(TestCase):
    """Creation is scope-based; existing visibility changes require dual managers/Admin."""

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

        self.internal_writer = user_model.objects.create_user(
            username="visibility-internal-writer",
            email="visibility-internal-writer@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.internal_writer, ROLE_INTERNAL_ARTICLE_WRITER)

        self.dual_approver = user_model.objects.create_user(
            username="visibility-dual-approver",
            email="visibility-dual-approver@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.dual_approver, ROLE_ARTICLE_APPROVER)
        assign_single_role_group(self.dual_approver, ROLE_INTERNAL_ARTICLE_APPROVER)

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
        self.internal_article = SuggestedArticle.objects.create(
            owner=self.internal_writer,
            title="Published internal visibility permission article",
            body="Published internal article body used for visibility permission tests.",
            keywords="internal, visibility",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.INTERNAL,
        )
        self.dual_writer_article = SuggestedArticle.objects.create(
            owner=self.dual_writer,
            title="Dual writer owned public article",
            body="A public article owned by a user who can create in both scopes.",
            keywords="writer, visibility",
            status=SuggestedArticle.Status.DRAFT,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )

    @staticmethod
    def _original_edit_view():
        view = edit_suggestion
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__
        return view

    def _request(self, method, user, path, data=None):
        if method == "post":
            request = self.factory.post(path, data=data or {})
        else:
            request = self.factory.get(path, data=data or {})
        request.user = user
        SessionMiddleware(lambda req: HttpResponse("ok")).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def _edit_request(self, method, user, article, data=None):
        return self._request(
            method,
            user,
            reverse("edit_suggestion", kwargs={"article_id": article.pk}),
            data=data,
        )

    def _edit_payload(self, article, *, visibility, status=None):
        return {
            "frm_kb_title": article.title,
            "frm_kb_body": article.body,
            "frm_kb_keywords": article.keywords,
            "article_visibility": visibility,
            "status": status or article.status,
            "submit_action": "save",
        }

    def test_creation_visibility_is_available_to_dual_writer(self):
        self.assertEqual(
            choose_requested_article_visibility(
                self.dual_writer,
                SuggestedArticle.Visibility.PUBLIC,
                action="add",
            ),
            SuggestedArticle.Visibility.PUBLIC,
        )
        self.assertEqual(
            choose_requested_article_visibility(
                self.dual_writer,
                SuggestedArticle.Visibility.INTERNAL,
                action="add",
            ),
            SuggestedArticle.Visibility.INTERNAL,
        )

    def test_single_scope_writer_cannot_forge_other_creation_scope(self):
        self.assertEqual(
            choose_requested_article_visibility(
                self.owner,
                SuggestedArticle.Visibility.INTERNAL,
                action="add",
            ),
            SuggestedArticle.Visibility.PUBLIC,
        )
        self.assertEqual(
            choose_requested_article_visibility(
                self.internal_writer,
                SuggestedArticle.Visibility.PUBLIC,
                action="add",
            ),
            SuggestedArticle.Visibility.INTERNAL,
        )

    def test_dual_writer_create_form_contains_both_visibility_choices(self):
        request = self._request("get", self.dual_writer, reverse("suggest"))

        with (
            patch("kb.views.suggestions.init_openkb_storage"),
            patch("kb.views.suggestions.render", return_value=HttpResponse("ok")) as render_mock,
        ):
            response = _suggest_unified(request)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertTrue(context["show_visibility_selector"])
        self.assertEqual(
            [choice["value"] for choice in context["visibility_choices"]],
            [SuggestedArticle.Visibility.PUBLIC, SuggestedArticle.Visibility.INTERNAL],
        )

    def test_single_scope_writer_create_form_is_fixed_to_own_scope(self):
        public_request = self._request("get", self.owner, reverse("suggest"))
        internal_request = self._request("get", self.internal_writer, reverse("suggest"))

        with (
            patch("kb.views.suggestions.init_openkb_storage"),
            patch("kb.views.suggestions.render", return_value=HttpResponse("ok")) as public_render,
        ):
            public_response = _suggest_unified(public_request)
        with (
            patch("kb.views.suggestions.init_openkb_storage"),
            patch("kb.views.suggestions.render", return_value=HttpResponse("ok")) as internal_render,
        ):
            internal_response = _suggest_unified(internal_request)

        self.assertEqual(public_response.status_code, 200)
        self.assertFalse(public_render.call_args.args[2]["show_visibility_selector"])
        self.assertEqual(public_render.call_args.args[2]["article_visibility"], SuggestedArticle.Visibility.PUBLIC)
        self.assertEqual(internal_response.status_code, 200)
        self.assertFalse(internal_render.call_args.args[2]["show_visibility_selector"])
        self.assertEqual(internal_render.call_args.args[2]["article_visibility"], SuggestedArticle.Visibility.INTERNAL)

    def test_only_admin_or_both_manager_roles_can_change_existing_visibility(self):
        self.assertTrue(user_can_change_article_visibility(self.admin, self.public_article))
        self.assertTrue(user_can_change_article_visibility(self.dual_manager, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.public_manager, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.internal_manager, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.dual_writer, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.dual_approver, self.public_article))
        self.assertFalse(user_can_change_article_visibility(self.owner, self.public_article))

    def test_dual_manager_edit_form_contains_both_visibility_choices_for_published_article(self):
        request = self._edit_request("get", self.dual_manager, self.public_article)

        with patch("kb.views.suggestions.render", return_value=HttpResponse("ok")) as render_mock:
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertTrue(context["can_change_article_visibility"])
        self.assertEqual(
            [choice["value"] for choice in context["visibility_choices"]],
            [SuggestedArticle.Visibility.PUBLIC, SuggestedArticle.Visibility.INTERNAL],
        )

    def test_admin_edit_form_contains_both_visibility_choices(self):
        request = self._edit_request("get", self.admin, self.public_article)

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
        request = self._edit_request("get", self.public_manager, self.public_article)

        with patch("kb.views.suggestions.render", return_value=HttpResponse("ok")) as render_mock:
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertFalse(context["can_change_article_visibility"])
        self.assertEqual(context["visibility_choices"], [])

    def test_forged_single_scope_manager_post_cannot_change_visibility(self):
        request = self._edit_request(
            "post",
            self.public_manager,
            self.public_article,
            data=self._edit_payload(
                self.public_article,
                visibility=SuggestedArticle.Visibility.INTERNAL,
                status=SuggestedArticle.Status.PUBLISHED,
            ),
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

    def test_forged_dual_writer_post_cannot_change_own_article_visibility(self):
        request = self._edit_request(
            "post",
            self.dual_writer,
            self.dual_writer_article,
            data=self._edit_payload(
                self.dual_writer_article,
                visibility=SuggestedArticle.Visibility.INTERNAL,
                status=SuggestedArticle.Status.DRAFT,
            ),
        )

        with (
            patch("kb.views.suggestions.write_article_files"),
            patch("kb.views.suggestions.sync_article_image_assets"),
            patch("kb.views.suggestions.clear_committed_pending_uploads"),
        ):
            response = self._original_edit_view()(request, self.dual_writer_article.pk)

        self.assertEqual(response.status_code, 302)
        self.dual_writer_article.refresh_from_db()
        self.assertEqual(self.dual_writer_article.visibility, SuggestedArticle.Visibility.PUBLIC)

    def test_dual_manager_can_change_published_public_article_to_internal(self):
        request = self._edit_request(
            "post",
            self.dual_manager,
            self.public_article,
            data=self._edit_payload(
                self.public_article,
                visibility=SuggestedArticle.Visibility.INTERNAL,
                status=SuggestedArticle.Status.PUBLISHED,
            ),
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

    def test_staged_published_update_defers_visibility_change_until_publish(self):
        payload = self._edit_payload(
            self.public_article,
            visibility=SuggestedArticle.Visibility.INTERNAL,
            status=SuggestedArticle.Status.PENDING,
        )
        payload["frm_kb_title"] = "Staged internal update title"
        payload["frm_kb_body"] = "Staged internal update body waiting for review."
        request = self._edit_request("post", self.dual_manager, self.public_article, data=payload)

        with (
            patch("kb.views.suggestions.delete_article_markdown_files") as delete_files,
            patch("kb.views.suggestions.write_article_files") as write_files,
            patch("kb.views.suggestions.sync_article_image_assets") as sync_images,
            patch("kb.views.suggestions.clear_committed_pending_uploads"),
        ):
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 302)
        self.public_article.refresh_from_db()
        self.assertEqual(self.public_article.status, SuggestedArticle.Status.PUBLISHED)
        # The currently approved article stays in its existing live scope while
        # the edited copy is only Pending. The authorised Manager/Admin can
        # select Internal again on the final Published action.
        self.assertEqual(self.public_article.visibility, SuggestedArticle.Visibility.PUBLIC)
        self.assertEqual(self.public_article.update_status, SuggestedArticle.UpdateStatus.PENDING)
        self.assertEqual(self.public_article.title, "Published visibility permission article")
        self.assertEqual(self.public_article.pending_update_title, "Staged internal update title")
        delete_files.assert_not_called()
        write_files.assert_not_called()
        sync_images.assert_not_called()

    def test_review_keep_pending_defers_visibility_change_until_publish(self):
        self.public_article.pending_update_title = "Owner submitted update title"
        self.public_article.pending_update_body = "Owner submitted update body waiting for review."
        self.public_article.pending_update_keywords = "owner, update"
        self.public_article.update_status = SuggestedArticle.UpdateStatus.PENDING
        self.public_article.capture_review_submission_snapshot(is_update=True)
        self.public_article.save(
            update_fields=[
                "pending_update_title",
                "pending_update_body",
                "pending_update_keywords",
                "update_status",
                "review_submission_snapshot",
            ]
        )

        payload = {
            "frm_kb_title": self.public_article.pending_update_title,
            "frm_kb_body": self.public_article.pending_update_body,
            "frm_kb_keywords": self.public_article.pending_update_keywords,
            "article_visibility": SuggestedArticle.Visibility.INTERNAL,
            "status": SuggestedArticle.Status.PENDING,
            "submit_action": "save",
            "editor_mode": "review",
        }
        request = self._edit_request("post", self.dual_manager, self.public_article, data=payload)

        with (
            patch("kb.views.suggestions.delete_article_markdown_files") as delete_files,
            patch("kb.views.suggestions.write_article_files") as write_files,
            patch("kb.views.suggestions.sync_article_image_assets") as sync_images,
            patch("kb.views.suggestions.clear_committed_pending_uploads"),
        ):
            response = self._original_edit_view()(request, self.public_article.pk)

        self.assertEqual(response.status_code, 302)
        self.public_article.refresh_from_db()
        self.assertEqual(self.public_article.visibility, SuggestedArticle.Visibility.PUBLIC)
        self.assertEqual(self.public_article.update_status, SuggestedArticle.UpdateStatus.PENDING)
        delete_files.assert_not_called()
        write_files.assert_not_called()
        sync_images.assert_not_called()

    def test_dual_manager_can_change_published_internal_article_to_public(self):
        request = self._edit_request(
            "post",
            self.dual_manager,
            self.internal_article,
            data=self._edit_payload(
                self.internal_article,
                visibility=SuggestedArticle.Visibility.PUBLIC,
                status=SuggestedArticle.Status.PUBLISHED,
            ),
        )

        with (
            patch("kb.views.suggestions.delete_article_markdown_files"),
            patch("kb.views.suggestions.write_article_files"),
            patch("kb.views.suggestions.sync_article_image_assets"),
            patch("kb.views.suggestions.clear_committed_pending_uploads"),
        ):
            response = self._original_edit_view()(request, self.internal_article.pk)

        self.assertEqual(response.status_code, 302)
        self.internal_article.refresh_from_db()
        self.assertEqual(self.internal_article.visibility, SuggestedArticle.Visibility.PUBLIC)
