import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from kb.models import ArticleImageUploadLog, SuggestedArticle
from kb.views.services import (
    get_openkb_uploads_dir,
    get_user_pending_image_upload_usage,
    user_owns_pending_article_image,
)


class PendingImageUploadQuotaTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="image-owner",
            email="image-owner@example.com",
            password="Different-Strong-Password-456!",
        )
        self.other = get_user_model().objects.create_user(
            username="other-owner",
            email="other-owner@example.com",
            password="Different-Strong-Password-789!",
        )
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

    def _pending_file(self, filename, data, owner=None):
        upload_dir = get_openkb_uploads_dir()
        (upload_dir / filename).write_bytes(data)
        ArticleImageUploadLog.objects.create(
            filename=filename,
            original_name=filename,
            content_type="image/png",
            size_bytes=len(data),
            uploaded_by=owner or self.owner,
        )

    def test_usage_is_derived_from_database_ownership_not_browser_session(self):
        self._pending_file("one.png", b"12345")
        self._pending_file("two.png", b"1234567")

        usage = get_user_pending_image_upload_usage(self.owner)

        self.assertEqual(usage["count"], 2)
        self.assertEqual(usage["bytes"], 12)
        self.assertEqual(set(usage["filenames"]), {"one.png", "two.png"})

    def test_committed_or_missing_files_do_not_consume_pending_quota(self):
        self._pending_file("committed.png", b"12345")
        self._pending_file("missing.png", b"12345")
        (get_openkb_uploads_dir() / "missing.png").unlink()
        SuggestedArticle.objects.create(
            owner=self.owner,
            title="Article with committed image",
            body="![image](/wiki/uploads/committed.png)",
            image_assets=["committed.png"],
            status=SuggestedArticle.Status.DRAFT,
            filename="committed-article.md",
        )

        usage = get_user_pending_image_upload_usage(self.owner)

        self.assertEqual(usage["count"], 0)
        self.assertEqual(usage["bytes"], 0)

    def test_pending_image_ownership_is_user_specific_and_uncommitted_only(self):
        self._pending_file("mine.png", b"12345")
        self._pending_file("theirs.png", b"12345", owner=self.other)

        self.assertTrue(user_owns_pending_article_image(self.owner, "mine.png"))
        self.assertFalse(user_owns_pending_article_image(self.owner, "theirs.png"))

        SuggestedArticle.objects.create(
            owner=self.owner,
            title="Committed owner image",
            body="![image](/wiki/uploads/mine.png)",
            image_assets=["mine.png"],
            status=SuggestedArticle.Status.DRAFT,
            filename="owner-image.md",
        )
        self.assertFalse(user_owns_pending_article_image(self.owner, "mine.png"))
