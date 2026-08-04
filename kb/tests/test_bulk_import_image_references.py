import io
import json
import tempfile
import zipfile
from pathlib import Path

from PIL import Image
from django.contrib.auth import get_user_model
from django.test import TestCase

from kb.models import SuggestedArticle
from kb.views.services_bulk import import_articles_from_zip


class BulkImportImageReferenceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="bulk-image-admin",
            email="bulk.image.admin@example.com",
            password="TestPassword123!",
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

    @staticmethod
    def _png_bytes():
        output = io.BytesIO()
        Image.new("RGB", (2, 2), (255, 255, 255)).save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _zip_bytes(manifest, uploads=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for filename, data in (uploads or {}).items():
                archive.writestr(f"uploads/{filename}", data)
        output.seek(0)
        return output

    def _manifest(self, title, body, image_assets=None):
        return {
            "format": "djopenkb-bulk-export-v1",
            "articles": [
                {
                    "title": title,
                    "body": body,
                    "image_assets": image_assets or [],
                    "status": SuggestedArticle.Status.PUBLISHED,
                    "visibility": SuggestedArticle.Visibility.PUBLIC,
                }
            ],
        }

    def test_only_linked_images_are_retained(self):
        manifest = self._manifest(
            "Linked image article",
            "![linked](/wiki/uploads/linked.png)",
            ["linked.png"],
        )
        archive = self._zip_bytes(
            manifest,
            {
                "linked.png": self._png_bytes(),
                "unused.png": self._png_bytes(),
            },
        )

        imported_count, errors = import_articles_from_zip(archive, self.owner)

        self.assertEqual(imported_count, 1)
        self.assertEqual(errors, [])
        article = SuggestedArticle.objects.get(title="Linked image article")
        self.assertEqual(len(article.image_assets), 1)
        self.assertIn(article.image_assets[0], article.body)
        upload_files = list((Path(self.temp_directory.name) / "openkb-data" / "wiki" / "uploads").iterdir())
        self.assertEqual([path.name for path in upload_files], article.image_assets)

    def test_missing_linked_image_skips_article(self):
        manifest = self._manifest(
            "Missing image article",
            "![missing](/wiki/uploads/missing.png)",
            ["missing.png"],
        )
        archive = self._zip_bytes(manifest)

        imported_count, errors = import_articles_from_zip(archive, self.owner)

        self.assertEqual(imported_count, 0)
        self.assertTrue(any("missing.png" in str(error) for error in errors))
        self.assertFalse(SuggestedArticle.objects.filter(title="Missing image article").exists())

    def test_image_copied_for_skipped_duplicate_article_is_cleaned(self):
        SuggestedArticle.objects.create(
            owner=self.owner,
            title="Duplicate image article",
            body="Existing body",
            status=SuggestedArticle.Status.PUBLISHED,
            filename="existing-duplicate.md",
        )
        manifest = self._manifest(
            "Duplicate image article",
            "![linked](/wiki/uploads/duplicate.png)",
            ["duplicate.png"],
        )
        archive = self._zip_bytes(manifest, {"duplicate.png": self._png_bytes()})

        imported_count, errors = import_articles_from_zip(archive, self.owner)

        self.assertEqual(imported_count, 0)
        self.assertTrue(any("duplicate title" in str(error).lower() for error in errors))
        upload_dir = Path(self.temp_directory.name) / "openkb-data" / "wiki" / "uploads"
        self.assertEqual(list(upload_dir.glob("*")), [])
