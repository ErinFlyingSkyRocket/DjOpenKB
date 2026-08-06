import base64
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from kb.models import ArticleEditWorkspace, ArticleImageUploadLog, SuggestedArticle
from kb.notifications import NOTIFICATION_KIND_UPDATE_SUBMISSION
from kb.permissions import (
    ROLE_ARTICLE_MANAGER,
    ROLE_ARTICLE_WRITER,
    assign_single_role_group,
    seed_djopenkb_role_groups,
)
from kb.views.services import get_openkb_uploads_dir, sync_article_image_assets


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


class ArticleEditWorkspaceTests(TestCase):
    def setUp(self):
        seed_djopenkb_role_groups()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="edit-checkpoint-writer",
            email="edit-checkpoint@example.invalid",
            password="Different-Strong-Password-123!",
        )
        assign_single_role_group(self.user, ROLE_ARTICLE_WRITER)
        self.other = user_model.objects.create_user(
            username="edit-checkpoint-other",
            email="edit-checkpoint-other@example.invalid",
            password="Different-Strong-Password-456!",
        )
        assign_single_role_group(self.other, ROLE_ARTICLE_WRITER)

        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        base = Path(self.temp_directory.name)
        self.settings_context = self.settings(
            OPENKB_DATA_DIR=base / "openkb-data",
            OPENKB_RAW_DIR=base / "openkb-data" / "raw",
            OPENKB_WIKI_DIR=base / "openkb-data" / "wiki",
            OPENKB_INTERNAL_DATA_DIR=base / "openkb-data-internal",
        )
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)

        self.article = SuggestedArticle.objects.create(
            owner=self.user,
            title="Editable checkpoint article",
            body="Original saved article body",
            keywords="original",
            status=SuggestedArticle.Status.DRAFT,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.client.force_login(self.user)

    def _edit_url(self, article=None):
        return reverse("edit_suggestion", args=[(article or self.article).pk])

    def _open_workspace(self, article=None):
        article = article or self.article
        response = self.client.get(self._edit_url(article))
        self.assertEqual(response.status_code, 200)
        workspace = ArticleEditWorkspace.objects.get(
            owner=self.user,
            article=article,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
        )
        return workspace, response

    def _autosave(self, workspace, **overrides):
        payload = {
            "edit_workspace_id": str(workspace.pk),
            "editor_mode": "edit",
            "frm_kb_title": "Autosaved title",
            "frm_kb_body": "Autosaved body",
            "frm_kb_keywords": "autosaved",
            "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            "status": "",
            "review_notes": "",
        }
        payload.update(overrides)
        return self.client.post(
            reverse("autosave_article_edit_workspace", args=[workspace.article_id]),
            payload,
        )

    def test_opening_edit_page_creates_private_checkpoint_from_saved_article(self):
        workspace, response = self._open_workspace()

        self.assertEqual(workspace.title, self.article.title)
        self.assertEqual(workspace.body, self.article.body)
        self.assertFalse(workspace.is_dirty)
        self.assertContains(response, str(workspace.pk))
        self.assertContains(response, "article-edit-workspace.js")
        self.assertContains(response, "articleEditWorkspaceLeaveModal")
        self.assertContains(response, "Reset edits")

    def test_autosave_changes_checkpoint_without_changing_article(self):
        workspace, _response = self._open_workspace()

        response = self._autosave(
            workspace,
            frm_kb_title="Incomplete autosaved edit",
            frm_kb_body="x",
        )

        self.assertEqual(response.status_code, 200)
        workspace.refresh_from_db()
        self.article.refresh_from_db()
        self.assertTrue(workspace.is_dirty)
        self.assertEqual(workspace.title, "Incomplete autosaved edit")
        self.assertEqual(workspace.body, "x")
        self.assertEqual(self.article.title, "Editable checkpoint article")
        self.assertEqual(self.article.body, "Original saved article body")

    def test_latest_autosave_wins_across_tabs(self):
        workspace, _response = self._open_workspace()
        first = self._autosave(workspace, frm_kb_title="First tab value")
        second = self._autosave(workspace, frm_kb_title="Second tab value")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        workspace.refresh_from_db()
        self.assertEqual(workspace.title, "Second tab value")

    def test_reopening_edit_page_restores_checkpoint(self):
        workspace, _response = self._open_workspace()
        self._autosave(
            workspace,
            frm_kb_title="Restored edit title",
            frm_kb_body="Restored edit body",
        )

        response = self.client.get(self._edit_url())

        self.assertContains(response, "Restored edit title")
        self.assertContains(response, "Restored edit body")
        self.assertContains(response, "Edit checkpoint restored")

    def test_discard_removes_checkpoint_and_uncommitted_image_only(self):
        workspace, _response = self._open_workspace()
        filename = "edit-checkpoint-only.png"
        file_path = get_openkb_uploads_dir() / filename
        file_path.write_bytes(PNG_1X1)
        ArticleImageUploadLog.objects.create(
            filename=filename,
            original_name=filename,
            content_type="image/png",
            size_bytes=len(PNG_1X1),
            edit_workspace_id=workspace.pk,
            editing_article_id=self.article.pk,
            uploaded_by=self.user,
        )
        workspace.body = f"Edited body\n![image](/wiki/uploads/{filename})"
        workspace.image_assets = [filename]
        workspace.owned_image_assets = [filename]
        workspace.is_dirty = True
        workspace.save()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("discard_article_edit_workspace", args=[self.article.pk]),
                {
                    "edit_workspace_id": str(workspace.pk),
                    "editor_mode": "edit",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ArticleEditWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(file_path.exists())
        self.article.refresh_from_db()
        self.assertEqual(self.article.body, "Original saved article body")

    def test_existing_article_image_is_not_physically_deleted_by_checkpoint_removal(self):
        filename = "existing-article-image.png"
        file_path = get_openkb_uploads_dir() / filename
        file_path.write_bytes(PNG_1X1)
        self.article.body = f"Original body\n![image](/wiki/uploads/{filename})"
        self.article.image_assets = [filename]
        self.article.save()
        workspace, _response = self._open_workspace()

        response = self.client.post(
            reverse("delete_article_image"),
            {
                "filename": filename,
                "article_id": str(self.article.pk),
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_body": "Original body",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["physical_deleted"])
        self.assertTrue(file_path.exists())

    def test_image_upload_is_owned_by_exact_edit_checkpoint(self):
        workspace, _response = self._open_workspace()
        upload = SimpleUploadedFile("checkpoint.png", PNG_1X1, content_type="image/png")

        response = self.client.post(
            reverse("upload_article_image"),
            {
                "image": upload,
                "article_id": str(self.article.pk),
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
            },
        )

        self.assertEqual(response.status_code, 200)
        filename = response.json()["filename"]
        workspace.refresh_from_db()
        self.assertIn(filename, workspace.owned_image_assets)
        self.assertTrue(
            ArticleImageUploadLog.objects.filter(
                filename=filename,
                edit_workspace_id=workspace.pk,
                editing_article_id=self.article.pk,
            ).exists()
        )

    def test_normal_save_commits_checkpoint_and_removes_workspace(self):
        workspace, _response = self._open_workspace()
        response = self.client.post(
            self._edit_url(),
            {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_title": "Saved checkpoint article",
                "frm_kb_body": "Saved checkpoint article body",
                "frm_kb_keywords": "saved, checkpoint",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "draft",
                "next": reverse("edit_my_suggestions"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "Saved checkpoint article")
        self.assertEqual(self.article.status, SuggestedArticle.Status.DRAFT)
        self.assertFalse(ArticleEditWorkspace.objects.filter(pk=workspace.pk).exists())


    def test_legacy_edit_post_without_workspace_id_uses_server_checkpoint(self):
        response = self.client.post(
            self._edit_url(),
            {
                "editor_mode": "edit",
                "frm_kb_title": "Compatible checkpoint save",
                "frm_kb_body": "Compatible checkpoint save body",
                "frm_kb_keywords": "compatible",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "draft",
                "next": reverse("edit_my_suggestions"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "Compatible checkpoint save")
        self.assertFalse(
            ArticleEditWorkspace.objects.filter(
                owner=self.user,
                article=self.article,
                editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
            ).exists()
        )

    def test_direct_image_sync_preserves_file_used_by_another_edit_checkpoint(self):
        filename = "other-editor-checkpoint-image.png"
        file_path = get_openkb_uploads_dir() / filename
        file_path.write_bytes(PNG_1X1)
        self.article.body = f"Original body\n![image](/wiki/uploads/{filename})"
        self.article.image_assets = [filename]
        self.article.save()
        ArticleEditWorkspace.objects.create(
            owner=self.other,
            article=self.article,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
            title=self.article.title,
            body=self.article.body,
            keywords=self.article.keywords,
            visibility=self.article.visibility,
            image_assets=[filename],
            is_dirty=True,
        )

        self.article.body = "Another editor removed the image."
        sync_article_image_assets(self.article, old_assets=[filename])

        self.assertTrue(file_path.exists())

    @patch("kb.views.suggestions.send_article_review_notification_after_commit")
    def test_published_update_submission_keeps_smtp_reviewer_notification(self, notify):
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = self.article.created_at
        self.article.save()
        workspace, _response = self._open_workspace()

        response = self.client.post(
            self._edit_url(),
            {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_title": "Published article edited title",
                "frm_kb_body": "Published article edited body",
                "frm_kb_keywords": "published, edit",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "submit_update",
                "next": reverse("edit_my_suggestions"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.update_status, SuggestedArticle.UpdateStatus.PENDING)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[2], NOTIFICATION_KIND_UPDATE_SUBMISSION)
        self.assertFalse(ArticleEditWorkspace.objects.filter(pk=workspace.pk).exists())


    def test_review_mode_uses_separate_checkpoint_without_changing_article(self):
        user_model = get_user_model()
        manager = user_model.objects.create_user(
            username="edit-checkpoint-manager",
            email="edit-checkpoint-manager@example.invalid",
            password="Different-Strong-Password-789!",
        )
        assign_single_role_group(manager, ROLE_ARTICLE_MANAGER)
        pending = SuggestedArticle.objects.create(
            owner=self.user,
            title="Pending review checkpoint article",
            body="Pending review checkpoint body",
            keywords="pending, review",
            status=SuggestedArticle.Status.PENDING,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.client.force_login(manager)
        response = self.client.get(self._edit_url(pending) + "?editor_mode=review")
        self.assertEqual(response.status_code, 200)
        workspace = ArticleEditWorkspace.objects.get(
            owner=manager,
            article=pending,
            editor_mode=ArticleEditWorkspace.EditorMode.REVIEW,
        )

        autosave = self.client.post(
            reverse("autosave_article_edit_workspace", args=[pending.pk]),
            {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "review",
                "frm_kb_title": "Reviewer checkpoint title",
                "frm_kb_body": "Reviewer checkpoint body",
                "frm_kb_keywords": "reviewer",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "status": SuggestedArticle.Status.FAILED,
                "review_notes": "Please revise this article.",
            },
        )

        self.assertEqual(autosave.status_code, 200)
        workspace.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(workspace.title, "Reviewer checkpoint title")
        self.assertEqual(workspace.status, SuggestedArticle.Status.FAILED)
        self.assertEqual(workspace.review_notes, "Please revise this article.")
        self.assertEqual(pending.title, "Pending review checkpoint article")
        self.assertEqual(pending.status, SuggestedArticle.Status.PENDING)

    def test_other_user_cannot_access_or_autosave_checkpoint(self):
        workspace, _response = self._open_workspace()
        self.client.force_login(self.other)

        edit_response = self.client.get(self._edit_url())
        autosave_response = self.client.post(
            reverse("autosave_article_edit_workspace", args=[self.article.pk]),
            {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_title": "Forged",
                "frm_kb_body": "Forged body",
                "frm_kb_keywords": "",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )

        self.assertEqual(edit_response.status_code, 404)
        self.assertEqual(autosave_response.status_code, 404)
        workspace.refresh_from_db()
        self.assertNotEqual(workspace.title, "Forged")
