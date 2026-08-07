import base64
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from kb.models import ArticleEditWorkspace, ArticleImageUploadLog, SuggestedArticle
from kb.notifications import NOTIFICATION_KIND_UPDATE_SUBMISSION
from kb.permissions import (
    ROLE_ADMIN_USERS,
    ROLE_ARTICLE_MANAGER,
    ROLE_ARTICLE_WRITER,
    assign_single_role_group,
    seed_djopenkb_role_groups,
)
from kb.views.services import (
    get_openkb_uploads_dir,
    sync_article_image_assets,
    validate_article_edit_workspace_image_references,
)


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

    def test_opening_edit_page_uses_manual_save_editor_context(self):
        workspace, response = self._open_workspace()

        self.assertEqual(workspace.title, self.article.title)
        self.assertEqual(workspace.body, self.article.body)
        self.assertFalse(workspace.is_dirty)
        self.assertContains(response, str(workspace.pk))
        self.assertNotContains(response, "article-edit-workspace.js")
        self.assertNotContains(response, "articleEditWorkspaceLeaveModal")
        self.assertNotContains(response, "Reset edits")
        self.assertNotContains(response, "Edit checkpoint saved")

    def test_published_editor_has_revert_without_reset_edits_button(self):
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now()
        self.article.save(update_fields=["status", "approved_at", "updated_at"])

        _workspace, response = self._open_workspace()

        self.assertContains(response, "Revert to last published version")
        self.assertNotContains(response, "Reset edits")
        self.assertNotContains(response, "Edit checkpoint saved")

    def _manager_for_article_workflow(self, username="article-workflow-manager"):
        manager = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password="Different-Strong-Password-Manager-123!",
        )
        assign_single_role_group(manager, ROLE_ARTICLE_MANAGER)
        return manager

    def test_manager_owner_draft_uses_full_manager_status_controls(self):
        manager = self._manager_for_article_workflow("manager-own-draft")
        owned = SuggestedArticle.objects.create(
            owner=manager,
            title="Manager owned draft article",
            body="Manager owned draft article body.",
            keywords="manager, draft",
            status=SuggestedArticle.Status.DRAFT,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.client.force_login(manager)

        response = self.client.get(self._edit_url(owned))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'option value="draft"')
        self.assertContains(response, 'option value="pending"')
        self.assertContains(response, 'option value="failed"')
        self.assertContains(response, 'option value="published"')
        self.assertNotContains(response, 'name="submit_action" value="submit"')

    def test_manager_published_editor_uses_shared_update_actions_not_personal_draft(self):
        manager = self._manager_for_article_workflow()
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now()
        self.article.save(update_fields=["status", "approved_at", "updated_at"])
        self.client.force_login(manager)

        response = self.client.get(self._edit_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="submit_update"')
        self.assertNotContains(response, 'value="save_personal_draft"')
        self.assertNotContains(response, 'value="revert_personal_draft"')
        self.assertNotContains(response, 'name="submit_action" value="save_update_draft"')
        self.assertContains(response, 'option value="draft"')
        self.assertContains(response, 'option value="pending"')
        self.assertContains(response, 'option value="failed"')
        self.assertContains(response, 'option value="published"')
        self.assertContains(response, "Saved update draft")
        self.assertContains(response, "Pending update")
        self.assertContains(response, "Update pending failed")
        self.assertNotContains(response, "article-edit-workspace.js")

    def test_manager_can_see_owner_saved_update_draft_as_shared_staging_copy(self):
        manager = self._manager_for_article_workflow()
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now()
        self.article.pending_update_title = "Owner shared unpublished title"
        self.article.pending_update_body = "Owner shared unpublished body saved before review."
        self.article.pending_update_keywords = "owner, shared"
        self.article.update_status = SuggestedArticle.UpdateStatus.NONE
        self.article.save()

        self.client.force_login(manager)
        response = self.client.get(self._edit_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Owner shared unpublished title")
        self.assertContains(response, "Owner shared unpublished body saved before review.")
        self.assertContains(response, 'option value="draft" selected')

    @patch("kb.views.suggestions.send_article_review_notification_after_commit")
    def test_manager_can_set_shared_pending_update_without_replacing_published_article(self, notify):
        manager = self._manager_for_article_workflow()
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now()
        published_title = self.article.title
        published_body = self.article.body
        self.article.save(update_fields=["status", "approved_at", "updated_at"])
        self.client.force_login(manager)
        open_response = self.client.get(self._edit_url())
        self.assertEqual(open_response.status_code, 200)
        workspace = ArticleEditWorkspace.objects.get(
            owner=manager,
            article=self.article,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
        )

        response = self.client.post(
            self._edit_url(),
            {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_title": "Manager shared pending update title",
                "frm_kb_body": "Manager shared pending update body waiting for review.",
                "frm_kb_keywords": "manager, shared, pending",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "status": SuggestedArticle.Status.PENDING,
                "submit_action": "save",
                "next": reverse("edit_my_suggestions"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(self.article.title, published_title)
        self.assertEqual(self.article.body, published_body)
        self.assertEqual(self.article.update_status, SuggestedArticle.UpdateStatus.PENDING)
        self.assertEqual(self.article.pending_update_title, "Manager shared pending update title")
        self.assertEqual(self.article.review_submission_snapshot.get("kind"), "update")
        notify.assert_called_once()

    def test_manager_normal_edit_of_pending_update_preserves_original_submission_snapshot(self):
        manager = self._manager_for_article_workflow("snapshot-preserving-manager")
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now()
        self.article.pending_update_title = "Owner submitted title"
        self.article.pending_update_body = "Owner submitted body that defines the reset baseline."
        self.article.pending_update_keywords = "owner, submitted"
        self.article.update_status = SuggestedArticle.UpdateStatus.PENDING
        self.article.update_submitted_at = timezone.now()
        self.article.capture_review_submission_snapshot(is_update=True)
        self.article.save()
        original_snapshot = dict(self.article.review_submission_snapshot)

        self.client.force_login(manager)
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 200)
        workspace = ArticleEditWorkspace.objects.get(
            owner=manager,
            article=self.article,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
        )

        with patch.multiple(
            "kb.views.suggestions",
            write_article_files=lambda article: None,
            sync_article_image_assets=lambda article, old_assets=None: None,
            clear_committed_pending_uploads=lambda request, assets: None,
        ):
            response = self.client.post(
                self._edit_url(),
                {
                    "edit_workspace_id": str(workspace.pk),
                    "editor_mode": "edit",
                    "frm_kb_title": "Manager edited pending title",
                    "frm_kb_body": "Manager edited the shared pending body without replacing the reset baseline.",
                    "frm_kb_keywords": "manager, pending",
                    "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                    "status": SuggestedArticle.Status.PENDING,
                    "submit_action": "save",
                    "next": reverse("edit_my_suggestions"),
                },
            )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.pending_update_title, "Manager edited pending title")
        self.assertEqual(self.article.review_submission_snapshot, original_snapshot)

    def test_manager_published_status_selector_maps_to_shared_update_states_and_publish(self):
        manager = self._manager_for_article_workflow()
        self.client.force_login(manager)

        for index, target_status in enumerate(
            [
                SuggestedArticle.Status.DRAFT,
                SuggestedArticle.Status.PENDING,
                SuggestedArticle.Status.FAILED,
                SuggestedArticle.Status.PUBLISHED,
            ],
            start=1,
        ):
            article = SuggestedArticle.objects.create(
                owner=self.user,
                title=f"Manager status article {index}",
                body=f"Published body for manager status article {index}.",
                keywords="manager, status",
                status=SuggestedArticle.Status.PUBLISHED,
                visibility=SuggestedArticle.Visibility.PUBLIC,
                approved_at=timezone.now(),
                pending_update_title=f"Shared staged title {index}",
                pending_update_body=f"Shared staged body {index} waiting for manager action.",
                pending_update_keywords="shared, staged",
                update_status=SuggestedArticle.UpdateStatus.NONE,
            )
            response = self.client.get(self._edit_url(article))
            self.assertEqual(response.status_code, 200)
            workspace = ArticleEditWorkspace.objects.get(
                owner=manager,
                article=article,
                editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
            )

            payload = {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_title": f"Manager applied title {index}",
                "frm_kb_body": f"Manager applied body {index} with direct status change.",
                "frm_kb_keywords": "manager, applied",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "status": target_status,
                "submit_action": "save",
                "next": reverse("edit_my_suggestions"),
            }
            if target_status == SuggestedArticle.Status.FAILED:
                payload["review_notes"] = "Manager marked this article pending failed."

            with patch.multiple(
                "kb.views.suggestions",
                write_article_files=lambda article: None,
                sync_article_image_assets=lambda article, old_assets=None: None,
                clear_committed_pending_uploads=lambda request, assets: None,
            ):
                response = self.client.post(self._edit_url(article), payload)

            self.assertEqual(response.status_code, 302, target_status)
            article.refresh_from_db()
            self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
            if target_status == SuggestedArticle.Status.PUBLISHED:
                self.assertEqual(article.title, f"Manager applied title {index}")
                self.assertEqual(article.pending_update_title, "")
                self.assertEqual(article.pending_update_body, "")
                self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.NONE)
            else:
                self.assertEqual(article.title, f"Manager status article {index}")
                self.assertEqual(article.pending_update_title, f"Manager applied title {index}")
                self.assertEqual(
                    article.pending_update_body,
                    f"Manager applied body {index} with direct status change.",
                )
                expected_update_status = {
                    SuggestedArticle.Status.DRAFT: SuggestedArticle.UpdateStatus.NONE,
                    SuggestedArticle.Status.PENDING: SuggestedArticle.UpdateStatus.PENDING,
                    SuggestedArticle.Status.FAILED: SuggestedArticle.UpdateStatus.FAILED,
                }[target_status]
                self.assertEqual(article.update_status, expected_update_status)
                if target_status == SuggestedArticle.Status.PENDING:
                    self.assertEqual(article.review_submission_snapshot.get("kind"), "update")

    def test_admin_published_editor_uses_same_shared_status_mapping(self):
        admin = get_user_model().objects.create_user(
            username="shared-update-admin",
            email="shared-update-admin@example.invalid",
            password="Different-Strong-Password-Admin-123!",
        )
        assign_single_role_group(admin, ROLE_ADMIN_USERS)
        article = SuggestedArticle.objects.create(
            owner=self.user,
            title="Admin shared status article",
            body="Published article body before the admin stages an update.",
            keywords="admin, shared",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
            approved_at=timezone.now(),
        )
        self.client.force_login(admin)

        response = self.client.get(self._edit_url(article))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'option value="draft"')
        self.assertContains(response, 'option value="pending"')
        self.assertContains(response, 'option value="failed"')
        self.assertContains(response, 'option value="published"')

        workspace = ArticleEditWorkspace.objects.get(
            owner=admin,
            article=article,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
        )
        with patch.multiple(
            "kb.views.suggestions",
            write_article_files=lambda current: None,
            sync_article_image_assets=lambda current, old_assets=None: None,
            clear_committed_pending_uploads=lambda request, assets: None,
        ):
            response = self.client.post(
                self._edit_url(article),
                {
                    "edit_workspace_id": str(workspace.pk),
                    "editor_mode": "edit",
                    "frm_kb_title": "Admin staged pending title",
                    "frm_kb_body": "Admin staged pending body that keeps the approved copy live.",
                    "frm_kb_keywords": "admin, pending",
                    "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                    "status": SuggestedArticle.Status.PENDING,
                    "submit_action": "save",
                    "next": reverse("edit_my_suggestions"),
                },
            )

        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.title, "Admin shared status article")
        self.assertEqual(article.pending_update_title, "Admin staged pending title")
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.PENDING)

    def test_manager_owner_can_save_shared_draft_with_status_and_revert(self):
        manager = self._manager_for_article_workflow("manager-owned-article")
        owned = SuggestedArticle.objects.create(
            owner=manager,
            title="Manager owned published article",
            body="Manager owned published article body.",
            keywords="manager, owner",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
            approved_at=timezone.now(),
        )
        self.client.force_login(manager)
        response = self.client.get(self._edit_url(owned))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="save_update_draft"')
        self.assertContains(response, 'value="revert_published"')
        self.assertNotContains(response, 'value="submit_update"')

        workspace = ArticleEditWorkspace.objects.get(
            owner=manager,
            article=owned,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
        )
        response = self.client.post(
            self._edit_url(owned),
            {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_title": "Manager owner saved draft title",
                "frm_kb_body": "Manager owner saved draft body that remains staged.",
                "frm_kb_keywords": "manager, owner, draft",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "status": SuggestedArticle.Status.DRAFT,
                "submit_action": "save",
                "next": reverse("edit_my_suggestions"),
            },
        )
        self.assertEqual(response.status_code, 302)
        owned.refresh_from_db()
        self.assertEqual(owned.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(owned.title, "Manager owned published article")
        self.assertEqual(owned.pending_update_title, "Manager owner saved draft title")
        self.assertEqual(owned.update_status, SuggestedArticle.UpdateStatus.NONE)

    def test_writer_save_draft_retracts_submitted_update_to_shared_saved_draft(self):
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now()
        self.article.pending_update_title = "Submitted update title"
        self.article.pending_update_body = "Submitted update body before returning to draft."
        self.article.pending_update_keywords = "submitted"
        self.article.update_status = SuggestedArticle.UpdateStatus.PENDING
        self.article.update_submitted_at = timezone.now()
        self.article.save()
        workspace, _response = self._open_workspace()

        response = self.client.post(
            self._edit_url(),
            {
                "edit_workspace_id": str(workspace.pk),
                "editor_mode": "edit",
                "frm_kb_title": "Shared update draft title",
                "frm_kb_body": "Shared update draft body after retracting from review.",
                "frm_kb_keywords": "shared, draft",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "save_update_draft",
                "next": reverse("edit_my_suggestions"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.update_status, SuggestedArticle.UpdateStatus.NONE)
        self.assertIsNone(self.article.update_submitted_at)
        self.assertIsNone(self.article.update_reviewed_at)
        self.assertEqual(self.article.pending_update_title, "Shared update draft title")
        self.assertEqual(
            self.article.pending_update_body,
            "Shared update draft body after retracting from review.",
        )
        self.assertFalse(self.article.has_pending_update)
        self.assertTrue(self.article.has_staged_update)

    def test_existing_article_autosave_endpoint_is_disabled(self):
        workspace, _response = self._open_workspace()

        response = self._autosave(
            workspace,
            frm_kb_title="Incomplete autosaved edit",
            frm_kb_body="x",
        )

        self.assertEqual(response.status_code, 404)
        workspace.refresh_from_db()
        self.article.refresh_from_db()
        self.assertFalse(workspace.is_dirty)
        self.assertEqual(workspace.title, self.article.title)
        self.assertEqual(workspace.body, self.article.body)
        self.assertEqual(self.article.title, "Editable checkpoint article")
        self.assertEqual(self.article.body, "Original saved article body")

    def test_reopening_edit_page_discards_unsaved_legacy_workspace(self):
        workspace, _response = self._open_workspace()
        old_workspace_id = workspace.pk
        workspace.title = "Old unsaved editor title"
        workspace.body = "Old unsaved editor body"
        workspace.is_dirty = True
        workspace.save(update_fields=["title", "body", "is_dirty", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(self._edit_url())

        replacement = ArticleEditWorkspace.objects.get(
            owner=self.user,
            article=self.article,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
        )
        self.assertNotEqual(replacement.pk, old_workspace_id)
        self.assertEqual(replacement.title, self.article.title)
        self.assertEqual(replacement.body, self.article.body)
        self.assertFalse(replacement.is_dirty)
        self.assertContains(response, self.article.title)
        self.assertContains(response, self.article.body)
        self.assertNotContains(response, "Old unsaved editor title")

    def test_reopening_edit_page_never_restores_previous_text_changes(self):
        workspace, _response = self._open_workspace()
        workspace.title = "Restored edit title"
        workspace.body = "Restored edit body"
        workspace.is_dirty = True
        workspace.save(update_fields=["title", "body", "is_dirty", "updated_at"])

        response = self.client.get(self._edit_url())

        self.assertContains(response, "Editable checkpoint article")
        self.assertContains(response, "Original saved article body")
        self.assertNotContains(response, "Restored edit title")
        self.assertNotContains(response, "Edit checkpoint restored")

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

    def test_edit_article_cannot_reuse_image_from_another_article(self):
        workspace, _response = self._open_workspace()
        filename = "other-article-edit-image.png"
        file_path = get_openkb_uploads_dir() / filename
        file_path.write_bytes(PNG_1X1)
        SuggestedArticle.objects.create(
            owner=self.other,
            title="Other article edit image owner",
            body=f"Other article body\n![image](/wiki/uploads/{filename})",
            image_assets=[filename],
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )

        with self.assertRaises(ValidationError):
            validate_article_edit_workspace_image_references(
                workspace,
                f"Edited body\n![image](/wiki/uploads/{filename})",
                self.article,
            )

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
        self.assertFalse(workspace.is_dirty)
        self.assertTrue(
            ArticleImageUploadLog.objects.filter(
                filename=filename,
                edit_workspace_id=workspace.pk,
                editing_article_id=self.article.pk,
            ).exists()
        )

    def test_newer_approval_blocks_older_editor_save_and_offers_reload(self):
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now() - timedelta(minutes=5)
        self.article.save(update_fields=["status", "approved_at", "updated_at"])
        workspace, _response = self._open_workspace()
        self.assertIsNotNone(workspace.article_approved_at_snapshot)

        approved_title = "Newly approved article version"
        approved_body = "This is the version approved while the old editor remained open."
        self.article.title = approved_title
        self.article.body = approved_body
        self.article.approved_at = timezone.now()
        self.article.save()

        response = self.client.post(
            self._edit_url(),
            {
                "edit_workspace_id": str(workspace.pk),
                # A forged browser timestamp must not override the server-owned
                # ArticleEditWorkspace approval baseline.
                "article_edit_approved_at_snapshot": (timezone.now() + timedelta(days=1)).isoformat(),
                "editor_mode": "edit",
                "frm_kb_title": "Older editor title",
                "frm_kb_body": "Older editor body that must not replace approval",
                "frm_kb_keywords": "older",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "save_update_draft",
                "next": reverse("edit_my_suggestions"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "approved or published while you were editing")
        self.assertContains(response, "Reload latest article")
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, approved_title)
        self.assertEqual(self.article.body, approved_body)
        self.assertEqual(self.article.status, SuggestedArticle.Status.PUBLISHED)
        workspace.refresh_from_db()
        self.assertEqual(workspace.title, "Editable checkpoint article")
        self.assertFalse(workspace.is_dirty)

    def test_deleted_old_checkpoint_returns_approval_conflict_instead_of_404(self):
        self.article.status = SuggestedArticle.Status.PUBLISHED
        self.article.approved_at = timezone.now() - timedelta(minutes=5)
        self.article.save(update_fields=["status", "approved_at", "updated_at"])
        workspace, _response = self._open_workspace()
        old_workspace_id = workspace.pk
        self.assertIsNotNone(workspace.article_approved_at_snapshot)
        workspace.delete()

        self.article.title = "Approved after previous submission"
        self.article.body = "Approved content remains authoritative."
        self.article.approved_at = timezone.now()
        self.article.save()

        response = self.client.post(
            self._edit_url(),
            {
                "edit_workspace_id": str(old_workspace_id),
                "article_edit_approved_at_snapshot": (timezone.now() + timedelta(days=1)).isoformat(),
                "editor_mode": "edit",
                "frm_kb_title": "Old tab continued editing",
                "frm_kb_body": "Old tab content",
                "frm_kb_keywords": "old-tab",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "save_update_draft",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reload latest article")
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "Approved after previous submission")

    def test_manual_edit_page_keeps_approval_snapshot_server_side_only(self):
        workspace, response = self._open_workspace()

        self.assertEqual(workspace.article_approved_at_snapshot, self.article.approved_at)
        self.assertNotContains(response, 'name="article_edit_approved_at_snapshot"')
        self.assertNotContains(response, "article-edit-workspace.js")
        self.assertNotContains(response, "articleEditWorkspaceResetButton")
        self.assertNotContains(response, "articleEditWorkspaceLeaveModal")

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
        self.assertEqual(self.article.review_submission_snapshot.get("kind"), "update")
        self.assertEqual(
            self.article.review_submission_snapshot.get("title"),
            "Published article edited title",
        )
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[2], NOTIFICATION_KIND_UPDATE_SUBMISSION)
        self.assertFalse(ArticleEditWorkspace.objects.filter(pk=workspace.pk).exists())


    def test_review_mode_is_manual_save_and_does_not_expose_autosave_ui(self):
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

        self.assertEqual(autosave.status_code, 404)
        workspace.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(workspace.title, pending.title)
        self.assertEqual(workspace.status, SuggestedArticle.Status.PENDING)
        self.assertFalse(workspace.is_dirty)
        self.assertEqual(pending.status, SuggestedArticle.Status.PENDING)
        self.assertNotContains(response, "article-edit-workspace.js")
        self.assertContains(response, 'data-review-action-button="true"')

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
