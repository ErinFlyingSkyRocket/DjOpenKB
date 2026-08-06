import base64
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from kb.models import ArticleCreationWorkspace, ArticleImageUploadLog, SuggestedArticle
from kb.permissions import ROLE_ARTICLE_WRITER, assign_single_role_group, seed_djopenkb_role_groups
from kb.views.services import (
    find_stray_uploaded_files,
    get_openkb_uploads_dir,
    get_user_pending_image_upload_usage,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


class ArticleCreationWorkspaceTests(TestCase):
    def setUp(self):
        seed_djopenkb_role_groups()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="workspace-writer",
            email="workspace-writer@example.invalid",
            password="Different-Strong-Password-123!",
        )
        assign_single_role_group(self.user, ROLE_ARTICLE_WRITER)
        self.other = user_model.objects.create_user(
            username="workspace-other",
            email="workspace-other@example.invalid",
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
        self.client.force_login(self.user)

    def _open_workspace(self):
        response = self.client.get(reverse("suggest"))
        self.assertEqual(response.status_code, 200)
        return ArticleCreationWorkspace.objects.get(owner=self.user), response

    def _workspace_version_fields(self, workspace, *, token="test-editor", sequence=1):
        workspace.refresh_from_db()
        return {
            "workspace_revision": str(workspace.revision),
            "workspace_editor_token": token,
            "workspace_save_sequence": str(sequence),
        }

    def _create_workspace_image(self, workspace, filename="workspace-image.png", data=PNG_1X1):
        upload_dir = get_openkb_uploads_dir()
        file_path = upload_dir / filename
        file_path.write_bytes(data)
        ArticleImageUploadLog.objects.create(
            filename=filename,
            original_name=filename,
            content_type="image/png",
            size_bytes=len(data),
            creation_workspace_id=workspace.pk,
            uploaded_by=workspace.owner,
        )
        workspace.image_assets = [filename]
        workspace.is_dirty = True
        workspace.save(update_fields=["image_assets", "is_dirty", "updated_at"])
        return file_path

    def test_opening_new_article_creates_one_reusable_temporary_workspace(self):
        workspace, response = self._open_workspace()

        self.assertContains(response, str(workspace.pk))
        self.assertContains(response, "article-creation-workspace.js")
        self.assertContains(response, "articleWorkspaceLeaveModal")

        second_response = self.client.get(reverse("suggest"))
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(ArticleCreationWorkspace.objects.filter(owner=self.user).count(), 1)
        self.assertContains(second_response, str(workspace.pk))

    def test_autosave_persists_fields_without_creating_visible_article(self):
        workspace, _response = self._open_workspace()

        response = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                **self._workspace_version_fields(workspace),
                "frm_kb_title": "Temporary title",
                "frm_kb_body": "Temporary body\nwith a second line",
                "frm_kb_keywords": "temporary, workspace",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )

        self.assertEqual(response.status_code, 200)
        workspace.refresh_from_db()
        self.assertTrue(workspace.is_dirty)
        self.assertEqual(workspace.title, "Temporary title")
        self.assertIn("second line", workspace.body)
        self.assertFalse(SuggestedArticle.objects.filter(owner=self.user).exists())

    def test_stale_browser_tab_cannot_overwrite_a_newer_checkpoint(self):
        workspace, _response = self._open_workspace()

        first = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "tab-a",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Newer checkpoint",
                "frm_kb_body": "Newer body",
                "frm_kb_keywords": "newer",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )
        stale = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "tab-b",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Stale overwrite",
                "frm_kb_body": "Stale body",
                "frm_kb_keywords": "stale",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(stale.status_code, 409)
        self.assertTrue(stale.json()["conflict"])
        workspace.refresh_from_db()
        self.assertEqual(workspace.title, "Newer checkpoint")
        self.assertEqual(workspace.revision, 1)

    def test_newer_save_from_same_editor_wins_when_requests_arrive_out_of_order(self):
        workspace, _response = self._open_workspace()

        first = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "same-tab",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Earlier snapshot",
                "frm_kb_body": "Earlier body",
                "frm_kb_keywords": "earlier",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )
        newer = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "same-tab",
                "workspace_save_sequence": "2",
                "frm_kb_title": "Latest snapshot",
                "frm_kb_body": "Latest body",
                "frm_kb_keywords": "latest",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )
        older_late = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "same-tab",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Older request arriving late",
                "frm_kb_body": "Older late body",
                "frm_kb_keywords": "older",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(newer.status_code, 200)
        self.assertEqual(older_late.status_code, 409)
        workspace.refresh_from_db()
        self.assertEqual(workspace.title, "Latest snapshot")
        self.assertEqual(workspace.revision, 2)
        self.assertEqual(workspace.last_editor_sequence, 2)

    def test_stale_tab_cannot_discard_a_newer_checkpoint(self):
        workspace, _response = self._open_workspace()
        saved = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "tab-a",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Protected checkpoint",
                "frm_kb_body": "Protected body",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )
        self.assertEqual(saved.status_code, 200)

        stale_discard = self.client.post(
            reverse("discard_article_creation_workspace"),
            {"workspace_id": str(workspace.pk), "workspace_revision": "0"},
        )

        self.assertEqual(stale_discard.status_code, 409)
        self.assertTrue(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())

    def test_confirmed_reset_discards_latest_checkpoint_without_manual_reload(self):
        workspace, _response = self._open_workspace()
        file_path = self._create_workspace_image(workspace, "reset-latest.png")
        saved = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "newer-tab",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Newer checkpoint that will be reset",
                "frm_kb_body": "Latest checkpoint body",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )
        self.assertEqual(saved.status_code, 200)

        with self.captureOnCommitCallbacks(execute=True):
            reset = self.client.post(
                reverse("discard_article_creation_workspace"),
                {
                    "workspace_id": str(workspace.pk),
                    # Deliberately stale values from the reset-confirming tab.
                    "workspace_revision": "0",
                    "workspace_editor_token": "stale-tab",
                    "workspace_save_sequence": "1",
                    "reset_latest": "1",
                },
            )

        self.assertEqual(reset.status_code, 200)
        self.assertTrue(reset.json()["discarded"])
        self.assertFalse(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(file_path.exists())

    def test_stale_tab_cannot_create_article_over_a_newer_checkpoint(self):
        workspace, _response = self._open_workspace()
        saved = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "tab-a",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Newest checkpoint title",
                "frm_kb_body": "Newest checkpoint body",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )
        self.assertEqual(saved.status_code, 200)

        stale_submit = self.client.post(
            reverse("suggest"),
            {
                "workspace_id": str(workspace.pk),
                "workspace_revision": "0",
                "workspace_editor_token": "tab-b",
                "workspace_save_sequence": "1",
                "frm_kb_title": "Stale article submission",
                "frm_kb_body": "This stale tab must not create an article.",
                "frm_kb_keywords": "stale",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "draft",
            },
        )

        self.assertEqual(stale_submit.status_code, 200)
        self.assertContains(stale_submit, "updated in another tab")
        self.assertContains(stale_submit, "Newest checkpoint title")
        self.assertFalse(SuggestedArticle.objects.filter(title="Stale article submission").exists())

    def test_other_user_cannot_autosave_or_discard_workspace(self):
        workspace, _response = self._open_workspace()
        self.client.force_login(self.other)

        autosave = self.client.post(
            reverse("autosave_article_creation_workspace"),
            {
                "workspace_id": str(workspace.pk),
                **self._workspace_version_fields(workspace),
                "frm_kb_title": "Forged",
                "frm_kb_body": "Forged body",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )
        discard = self.client.post(
            reverse("discard_article_creation_workspace"),
            {"workspace_id": str(workspace.pk), **self._workspace_version_fields(workspace)},
        )

        self.assertEqual(autosave.status_code, 404)
        # Discard is intentionally idempotent and does not reveal whether a
        # guessed UUID belongs to another user.
        self.assertEqual(discard.status_code, 200)
        self.assertTrue(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())

    def test_image_upload_is_bound_to_workspace_and_counts_as_pending(self):
        workspace, _response = self._open_workspace()
        image = SimpleUploadedFile("pasted.png", PNG_1X1, content_type="image/png")

        response = self.client.post(
            reverse("upload_article_image"),
            {"workspace_id": str(workspace.pk), "image": image},
        )

        self.assertEqual(response.status_code, 200)
        filename = response.json()["filename"]
        workspace.refresh_from_db()
        self.assertIn(filename, workspace.image_assets)
        self.assertTrue(workspace.is_dirty)
        self.assertTrue(
            ArticleImageUploadLog.objects.filter(
                filename=filename,
                creation_workspace_id=workspace.pk,
            ).exists()
        )
        usage = get_user_pending_image_upload_usage(self.user)
        self.assertIn(filename, usage["filenames"])
        self.assertNotIn(filename, {item["filename"] for item in find_stray_uploaded_files(0)})


    def test_image_upload_requires_an_exact_authorised_editor_context(self):
        no_context = self.client.post(
            reverse("upload_article_image"),
            {"image": SimpleUploadedFile("no-context.png", PNG_1X1, content_type="image/png")},
        )
        self.assertEqual(no_context.status_code, 404)

        article = SuggestedArticle.objects.create(
            owner=self.user,
            title="Existing draft upload context",
            body="Existing article body",
            visibility=SuggestedArticle.Visibility.PUBLIC,
            status=SuggestedArticle.Status.DRAFT,
        )
        allowed = self.client.post(
            reverse("upload_article_image"),
            {
                "article_id": str(article.pk),
                "editor_mode": "edit",
                "image": SimpleUploadedFile("article-context.png", PNG_1X1, content_type="image/png"),
            },
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(
            ArticleImageUploadLog.objects.filter(
                filename=allowed.json()["filename"],
                editing_article_id=article.pk,
            ).exists()
        )

        other_article = SuggestedArticle.objects.create(
            owner=self.other,
            title="Other user's private draft",
            body="Other private article body",
            visibility=SuggestedArticle.Visibility.PUBLIC,
            status=SuggestedArticle.Status.DRAFT,
        )
        forged = self.client.post(
            reverse("upload_article_image"),
            {
                "article_id": str(other_article.pk),
                "editor_mode": "edit",
                "image": SimpleUploadedFile("forged-context.png", PNG_1X1, content_type="image/png"),
            },
        )
        self.assertEqual(forged.status_code, 404)

    def test_discard_deletes_workspace_and_all_uncommitted_images_immediately(self):
        workspace, _response = self._open_workspace()
        file_path = self._create_workspace_image(workspace)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("discard_article_creation_workspace"),
                {"workspace_id": str(workspace.pk), **self._workspace_version_fields(workspace)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(file_path.exists())

    def test_saving_draft_preserves_referenced_image_and_removes_unused_workspace_image(self):
        workspace, _response = self._open_workspace()
        referenced_path = self._create_workspace_image(workspace, "referenced.png")
        unused_path = get_openkb_uploads_dir() / "unused.png"
        unused_path.write_bytes(PNG_1X1)
        ArticleImageUploadLog.objects.create(
            filename="unused.png",
            original_name="unused.png",
            content_type="image/png",
            size_bytes=len(PNG_1X1),
            creation_workspace_id=workspace.pk,
            uploaded_by=self.user,
        )
        workspace.image_assets = ["referenced.png", "unused.png"]
        workspace.save(update_fields=["image_assets", "updated_at"])

        with (
            patch("kb.views.suggestions.write_article_files"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("suggest"),
                {
                    "workspace_id": str(workspace.pk),
                    **self._workspace_version_fields(workspace),
                    "frm_kb_title": "Workspace draft article",
                    "frm_kb_body": "A valid body with image.\n\n![image](/wiki/uploads/referenced.png)",
                    "frm_kb_keywords": "workspace, draft",
                    "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                    "submit_action": "draft",
                },
            )

        self.assertEqual(response.status_code, 302)
        article = SuggestedArticle.objects.get(owner=self.user, title="Workspace draft article")
        self.assertEqual(article.status, SuggestedArticle.Status.DRAFT)
        self.assertEqual(article.image_assets, ["referenced.png"])
        self.assertFalse(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertTrue(referenced_path.exists())
        self.assertFalse(unused_path.exists())


    def test_automatic_cleanup_preserves_old_persistent_workspace_and_removes_only_orphans(self):
        workspace, _response = self._open_workspace()
        checkpoint_path = self._create_workspace_image(workspace, "persistent-checkpoint.png")
        ArticleCreationWorkspace.objects.filter(pk=workspace.pk).update(
            updated_at=timezone.now() - timedelta(days=365)
        )
        os.utime(checkpoint_path, (1, 1))

        orphan_path = get_openkb_uploads_dir() / "interrupted-orphan.png"
        orphan_path.write_bytes(PNG_1X1)
        os.utime(orphan_path, (1, 1))

        call_command(
            "cleanup_stray_upload_files",
            min_age_minutes=0,
            noinput=True,
            verbosity=0,
        )

        self.assertTrue(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertTrue(checkpoint_path.exists())
        self.assertFalse(orphan_path.exists())



    def test_newer_checkpoint_saved_during_article_finalisation_is_preserved(self):
        workspace, _response = self._open_workspace()

        def simulate_newer_checkpoint(*_args, **_kwargs):
            ArticleCreationWorkspace.objects.filter(pk=workspace.pk).update(
                title="Next checkpoint after submission",
                body="This content belongs to the next New Article checkpoint.",
                is_dirty=True,
                revision=2,
                last_editor_token="other-tab",
                last_editor_sequence=1,
            )

        with (
            patch("kb.views.suggestions.write_article_files"),
            patch(
                "kb.views.suggestions.sync_article_image_assets",
                side_effect=simulate_newer_checkpoint,
            ),
        ):
            response = self.client.post(
                reverse("suggest"),
                {
                    "workspace_id": str(workspace.pk),
                    "workspace_revision": "0",
                    "workspace_editor_token": "submit-tab",
                    "workspace_save_sequence": "1",
                    "frm_kb_title": "Submitted while another tab continues",
                    "frm_kb_body": "This valid article is saved from one browser tab.",
                    "frm_kb_keywords": "concurrency",
                    "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                    "submit_action": "draft",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            SuggestedArticle.objects.filter(
                title="Submitted while another tab continues"
            ).exists()
        )
        workspace.refresh_from_db()
        self.assertEqual(workspace.title, "Next checkpoint after submission")
        self.assertEqual(workspace.revision, 2)

    def test_workspace_body_cannot_claim_another_users_uncommitted_image(self):
        workspace, _response = self._open_workspace()
        other_workspace = ArticleCreationWorkspace.objects.create(
            owner=self.other,
            title="Other temporary article",
            is_dirty=True,
        )
        other_file = get_openkb_uploads_dir() / "other-user-pending.png"
        other_file.write_bytes(PNG_1X1)
        ArticleImageUploadLog.objects.create(
            filename="other-user-pending.png",
            original_name="other-user-pending.png",
            content_type="image/png",
            size_bytes=len(PNG_1X1),
            creation_workspace_id=other_workspace.pk,
            uploaded_by=self.other,
        )
        other_workspace.image_assets = ["other-user-pending.png"]
        other_workspace.save(update_fields=["image_assets", "updated_at"])

        response = self.client.post(
            reverse("suggest"),
            {
                "workspace_id": str(workspace.pk),
                **self._workspace_version_fields(workspace),
                "frm_kb_title": "Forged image draft",
                "frm_kb_body": "A valid body.\n\n![image](/wiki/uploads/other-user-pending.png)",
                "frm_kb_keywords": "security",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                "submit_action": "draft",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not belong to this temporary workspace")
        self.assertFalse(SuggestedArticle.objects.filter(title="Forged image draft").exists())
        self.assertTrue(other_file.exists())
        self.assertTrue(ArticleCreationWorkspace.objects.filter(pk=other_workspace.pk).exists())

    def test_discard_does_not_delete_image_only_typed_into_workspace_body(self):
        workspace, _response = self._open_workspace()
        other_workspace = ArticleCreationWorkspace.objects.create(owner=self.other, is_dirty=True)
        other_file = self._create_workspace_image(
            other_workspace,
            filename="other-workspace-owned.png",
        )
        workspace.body = "![image](/wiki/uploads/other-workspace-owned.png)"
        workspace.is_dirty = True
        workspace.save(update_fields=["body", "is_dirty", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("discard_article_creation_workspace"),
                {"workspace_id": str(workspace.pk), **self._workspace_version_fields(workspace)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(other_file.exists())
        self.assertTrue(ArticleCreationWorkspace.objects.filter(pk=other_workspace.pk).exists())

    def test_workspace_visibility_is_reset_when_user_no_longer_has_that_scope(self):
        workspace, _response = self._open_workspace()
        workspace.visibility = SuggestedArticle.Visibility.INTERNAL
        workspace.title = "Preserved temporary content"
        workspace.is_dirty = True
        workspace.save(update_fields=["visibility", "title", "is_dirty", "updated_at"])

        response = self.client.get(reverse("suggest"))

        self.assertEqual(response.status_code, 200)
        workspace.refresh_from_db()
        self.assertEqual(workspace.visibility, SuggestedArticle.Visibility.PUBLIC)
        self.assertEqual(workspace.title, "Preserved temporary content")

    def test_active_workspace_image_is_not_reported_as_stray_even_when_file_is_old(self):
        workspace, _response = self._open_workspace()
        file_path = self._create_workspace_image(workspace, "old-workspace.png")
        old_timestamp = 1
        os.utime(file_path, (old_timestamp, old_timestamp))

        stray = find_stray_uploaded_files(min_age_minutes=0)

        self.assertNotIn("old-workspace.png", {item["filename"] for item in stray})

    def test_leave_modal_is_blocking_and_offers_checkpoint_or_discard(self):
        _workspace, response = self._open_workspace()

        html = response.content.decode("utf-8")
        modal = html.split('id="articleWorkspaceLeaveModal"', 1)[1].split(
            'id="articleWorkspaceResetModal"', 1
        )[0]
        self.assertIn('data-backdrop="static"', modal)
        self.assertIn('data-keyboard="false"', modal)
        self.assertIn("Keep checkpoint and continue", modal)
        self.assertIn("Discard and continue", modal)
        self.assertNotIn("Save as draft", modal)
        self.assertNotIn("Continue editing", modal)
        self.assertNotIn('class="close"', modal)
        self.assertNotIn('data-dismiss="modal"', modal)

        javascript = (
            Path(__file__).resolve().parents[3]
            / "website"
            / "static"
            / "javascripts"
            / "article-creation-workspace.js"
        ).read_text(encoding="utf-8")
        self.assertIn('backdrop: "static"', javascript)
        self.assertIn('keyboard: false', javascript)
        self.assertIn('saveWorkspace()', javascript)
        self.assertIn('discardWorkspace()', javascript)
        self.assertIn('workspace_revision', javascript)
        self.assertIn('workspace_editor_token', javascript)
        self.assertIn('workspace_save_sequence', javascript)
        self.assertIn('response.status === 409', javascript)
        self.assertNotIn('workspace_leave_action', javascript)
        self.assertNotIn('saveWorkspaceAsDraft', javascript)

        template = (
            Path(__file__).resolve().parents[3]
            / "website"
            / "templates"
            / "suggest.html"
        ).read_text(encoding="utf-8")
        self.assertIn("if (!response.ok || !payload.deleted)", template)
        self.assertIn("setEditorValue(originalBody)", template)

    def test_reset_control_starts_a_blank_workspace_after_discard(self):
        workspace, response = self._open_workspace()
        workspace.title = "Checkpoint to reset"
        workspace.body = "Temporary body"
        workspace.is_dirty = True
        workspace.save(update_fields=["title", "body", "is_dirty", "updated_at"])
        file_path = self._create_workspace_image(workspace, "reset-workspace.png")

        html = response.content.decode("utf-8")
        self.assertIn('id="articleWorkspaceResetButton"', html)
        self.assertIn('id="articleWorkspaceResetModal"', html)
        self.assertIn('id="articleWorkspaceResetConfirmButton"', html)

        with self.captureOnCommitCallbacks(execute=True):
            discard = self.client.post(
                reverse("discard_article_creation_workspace"),
                {"workspace_id": str(workspace.pk), **self._workspace_version_fields(workspace)},
            )
        self.assertEqual(discard.status_code, 200)
        self.assertFalse(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(file_path.exists())

        reopened = self.client.get(reverse("suggest"))
        self.assertEqual(reopened.status_code, 200)
        new_workspace = ArticleCreationWorkspace.objects.get(owner=self.user)
        self.assertNotEqual(new_workspace.pk, workspace.pk)
        self.assertEqual(new_workspace.title, "")
        self.assertEqual(new_workspace.body, "")
        self.assertFalse(new_workspace.is_dirty)

    def test_direct_navigation_recovery_is_supported_by_autosave_and_unload_flush(self):
        _workspace, _response = self._open_workspace()
        javascript = (
            Path(__file__).resolve().parents[3]
            / "website"
            / "static"
            / "javascripts"
            / "article-creation-workspace.js"
        ).read_text(encoding="utf-8")

        self.assertIn('window.addEventListener("pagehide"', javascript)
        self.assertIn("navigator.sendBeacon", javascript)
        self.assertIn('keepalive: true', javascript)
        self.assertIn('window.addEventListener("beforeunload"', javascript)
        self.assertIn('document.addEventListener("visibilitychange"', javascript)

    def test_checkpoint_autosave_does_not_create_real_draft_or_notify_reviewers(self):
        workspace, _response = self._open_workspace()

        with patch("kb.views.suggestions.send_article_review_notification_after_commit") as notify:
            response = self.client.post(
                reverse("autosave_article_creation_workspace"),
                {
                    "workspace_id": str(workspace.pk),
                    **self._workspace_version_fields(workspace),
                    "frm_kb_title": "Saved checkpoint",
                    "frm_kb_body": "This body remains a private checkpoint.",
                    "frm_kb_keywords": "workspace, checkpoint",
                    "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                },
            )

        self.assertEqual(response.status_code, 200)
        workspace.refresh_from_db()
        self.assertEqual(workspace.title, "Saved checkpoint")
        self.assertTrue(workspace.is_dirty)
        self.assertFalse(SuggestedArticle.objects.filter(owner=self.user).exists())
        notify.assert_not_called()

    def test_normal_submit_still_notifies_article_reviewers(self):
        workspace, _response = self._open_workspace()

        with (
            patch("kb.views.suggestions.write_article_files"),
            patch("kb.views.suggestions.send_article_review_notification_after_commit") as notify,
        ):
            response = self.client.post(
                reverse("suggest"),
                {
                    "workspace_id": str(workspace.pk),
                    **self._workspace_version_fields(workspace),
                    "frm_kb_title": "Submitted workspace article",
                    "frm_kb_body": "This valid body enters the normal review workflow.",
                    "frm_kb_keywords": "workspace, review",
                    "article_visibility": SuggestedArticle.Visibility.PUBLIC,
                    "submit_action": "submit",
                },
            )

        self.assertEqual(response.status_code, 302)
        article = SuggestedArticle.objects.get(
            owner=self.user,
            title="Submitted workspace article",
        )
        self.assertEqual(article.status, SuggestedArticle.Status.PENDING)
        notify.assert_called_once()

