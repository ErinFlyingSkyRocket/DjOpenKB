import base64
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase

from kb.models import ActivityLog, ArticleCreationWorkspace, ArticleEditWorkspace, ArticleImageUploadLog, SuggestedArticle
from kb.permissions import ROLE_ARTICLE_WRITER, ROLE_DISABLED_USER, assign_single_role_group, seed_djopenkb_role_groups
from kb.views.services import get_openkb_uploads_dir


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


class UserAccountDeletionCheckpointCleanupTests(TestCase):
    def setUp(self):
        seed_djopenkb_role_groups()
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            username="delete-workspace-user",
            email="delete-workspace-user@example.invalid",
            password="Different-Strong-Password-123!",
        )
        assign_single_role_group(self.user, ROLE_ARTICLE_WRITER)

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

    def _create_checkpoint_with_image(self, filename="account-delete-checkpoint.png"):
        workspace = ArticleCreationWorkspace.objects.create(
            owner=self.user,
            title="Unfinished private checkpoint",
            body=f"Private checkpoint body\n![image](/wiki/uploads/{filename})",
            keywords="private, unfinished",
            image_assets=[filename],
            is_dirty=True,
        )
        file_path = get_openkb_uploads_dir() / filename
        file_path.write_bytes(PNG_1X1)
        ArticleImageUploadLog.objects.create(
            filename=filename,
            original_name="private-original-name.png",
            content_type="image/png",
            size_bytes=len(PNG_1X1),
            creation_workspace_id=workspace.pk,
            uploaded_by=self.user,
        )
        ActivityLog.objects.create(
            event_type=ActivityLog.EventType.IMAGE_UPLOADED,
            user=self.user,
            username=self.user.username,
            details={
                "filename": filename,
                "workspace_id": str(workspace.pk),
                "editor_context": "workspace",
            },
        )
        return workspace, file_path

    def test_permanent_user_delete_purges_checkpoint_file_and_checkpoint_logs(self):
        workspace, file_path = self._create_checkpoint_with_image()
        client = Client()
        client.force_login(self.user)
        session_key = client.session.session_key

        published = SuggestedArticle.objects.create(
            owner=self.user,
            title="Article preserved after account deletion",
            body="Published article body",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        user_id = self.user.pk

        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()

        self.assertFalse(self.user_model.objects.filter(pk=user_id).exists())
        self.assertFalse(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(file_path.exists())
        self.assertFalse(
            ArticleImageUploadLog.objects.filter(creation_workspace_id=workspace.pk).exists()
        )
        self.assertFalse(
            ActivityLog.objects.filter(
                event_type=ActivityLog.EventType.IMAGE_UPLOADED,
                details__workspace_id=str(workspace.pk),
            ).exists()
        )
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

        published.refresh_from_db()
        self.assertIsNone(published.owner)
        self.assertEqual(published.author_username_snapshot, "delete-workspace-user")

    def test_account_deletion_does_not_remove_file_used_by_existing_article(self):
        workspace, file_path = self._create_checkpoint_with_image("shared-published-image.png")
        article = SuggestedArticle.objects.create(
            owner=self.user,
            title="Published image must remain",
            body="![image](/wiki/uploads/shared-published-image.png)",
            image_assets=["shared-published-image.png"],
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        committed_log = ArticleImageUploadLog.objects.create(
            filename="separate-committed-image.png",
            original_name="separate-committed-image.png",
            content_type="image/png",
            size_bytes=len(PNG_1X1),
            editing_article_id=article.pk,
            uploaded_by=self.user,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()

        article.refresh_from_db()
        self.assertIsNone(article.owner)
        self.assertTrue(file_path.exists())
        self.assertFalse(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(
            ArticleImageUploadLog.objects.filter(creation_workspace_id=workspace.pk).exists()
        )
        committed_log.refresh_from_db()
        self.assertEqual(committed_log.uploader_display, "delete-workspace-user")


    def test_permanent_user_delete_purges_existing_article_edit_checkpoint(self):
        article = SuggestedArticle.objects.create(
            owner=self.user,
            title="Saved article kept after edit checkpoint deletion",
            body="Saved article body",
            status=SuggestedArticle.Status.DRAFT,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        filename = "account-delete-edit-checkpoint.png"
        workspace = ArticleEditWorkspace.objects.create(
            owner=self.user,
            article=article,
            editor_mode=ArticleEditWorkspace.EditorMode.EDIT,
            title="Private unsaved edit",
            body=f"Private edit body\n![image](/wiki/uploads/{filename})",
            image_assets=[filename],
            owned_image_assets=[filename],
            is_dirty=True,
        )
        file_path = get_openkb_uploads_dir() / filename
        file_path.write_bytes(PNG_1X1)
        ArticleImageUploadLog.objects.create(
            filename=filename,
            original_name=filename,
            content_type="image/png",
            size_bytes=len(PNG_1X1),
            edit_workspace_id=workspace.pk,
            editing_article_id=article.pk,
            uploaded_by=self.user,
        )
        ActivityLog.objects.create(
            event_type=ActivityLog.EventType.IMAGE_UPLOADED,
            user=self.user,
            username=self.user.username,
            details={
                "filename": filename,
                "edit_workspace_id": str(workspace.pk),
                "article_id": article.pk,
                "editor_context": "edit_workspace",
            },
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()

        self.assertFalse(ArticleEditWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(file_path.exists())
        self.assertFalse(
            ArticleImageUploadLog.objects.filter(edit_workspace_id=workspace.pk).exists()
        )
        self.assertFalse(SuggestedArticle.objects.filter(pk=article.pk).exists())

    def test_permanent_user_delete_removes_all_unpublished_saved_articles(self):
        draft = SuggestedArticle.objects.create(
            owner=self.user,
            title="Private draft removed with account",
            body="Private draft body",
            status=SuggestedArticle.Status.DRAFT,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        pending = SuggestedArticle.objects.create(
            owner=self.user,
            title="Private pending removed with account",
            body="Private pending body",
            status=SuggestedArticle.Status.PENDING,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        failed = SuggestedArticle.objects.create(
            owner=self.user,
            title="Private failed removed with account",
            body="Private failed body",
            status=SuggestedArticle.Status.FAILED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        published = SuggestedArticle.objects.create(
            owner=self.user,
            title="Published knowledge survives account deletion",
            body="Published knowledge body",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()

        self.assertFalse(
            SuggestedArticle.objects.filter(pk__in=[draft.pk, pending.pk, failed.pk]).exists()
        )
        published.refresh_from_db()
        self.assertIsNone(published.owner)
        self.assertEqual(published.body, "Published knowledge body")
        self.assertEqual(published.author_username_snapshot, "delete-workspace-user")

    def test_permanent_user_delete_clears_unpublished_update_from_preserved_article(self):
        filename = "deleted-owner-pending-update.png"
        file_path = get_openkb_uploads_dir() / filename
        file_path.write_bytes(PNG_1X1)
        published = SuggestedArticle.objects.create(
            owner=self.user,
            title="Published article with private update",
            body="Published body remains",
            keywords="published",
            image_assets=[],
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
            pending_update_title="Private pending update title",
            pending_update_body=f"Private pending update body\n![image](/wiki/uploads/{filename})",
            pending_update_keywords="private",
            pending_update_image_assets=[filename],
            update_status=SuggestedArticle.UpdateStatus.PENDING,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()

        published.refresh_from_db()
        self.assertIsNone(published.owner)
        self.assertEqual(published.body, "Published body remains")
        self.assertEqual(published.pending_update_title, "")
        self.assertEqual(published.pending_update_body, "")
        self.assertEqual(published.pending_update_keywords, "")
        self.assertEqual(published.pending_update_image_assets, [])
        self.assertEqual(published.update_status, SuggestedArticle.UpdateStatus.NONE)
        self.assertIsNone(published.update_submitted_at)
        self.assertIsNone(published.update_reviewed_at)
        self.assertFalse(file_path.exists())

    def test_inactive_account_preserves_checkpoint_and_image(self):
        workspace, file_path = self._create_checkpoint_with_image("inactive-checkpoint.png")

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertTrue(self.user_model.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertTrue(file_path.exists())
        self.assertTrue(
            ArticleImageUploadLog.objects.filter(creation_workspace_id=workspace.pk).exists()
        )

    def test_disabled_role_preserves_checkpoint_and_image(self):
        workspace, file_path = self._create_checkpoint_with_image("disabled-checkpoint.png")

        assign_single_role_group(self.user, ROLE_DISABLED_USER)

        self.assertTrue(self.user_model.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(ArticleCreationWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertTrue(file_path.exists())
        self.assertTrue(
            ArticleImageUploadLog.objects.filter(creation_workspace_id=workspace.pk).exists()
        )
