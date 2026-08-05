import io
import zipfile

from django.test import TestCase, override_settings

from kb.models import SuggestedArticle
from kb.views.services_bulk import _read_bulk_manifest, validate_bulk_manifest, validate_split_manifest


@override_settings(OPENKB_WIKI_DIR="/tmp/openkb-test-wiki")
class BulkImportSchemaValidationTests(TestCase):
    def valid_manifest(self):
        return {
            "format": "djopenkb-bulk-export-v1",
            "article_count": 1,
            "articles": [
                {
                    "title": "Imported article",
                    "body": "Validated content",
                    "keywords": "test",
                    "visibility": SuggestedArticle.Visibility.PUBLIC,
                    "status": SuggestedArticle.Status.PUBLISHED,
                    "update_status": SuggestedArticle.UpdateStatus.NONE,
                    "image_assets": [],
                    "pending_update_image_assets": [],
                    "review_notes_history": [],
                }
            ],
        }

    def test_valid_export_manifest_is_normalized(self):
        rows = validate_bulk_manifest(self.valid_manifest())
        self.assertEqual(rows[0]["title"], "Imported article")

    def test_unknown_article_fields_are_rejected(self):
        manifest = self.valid_manifest()
        manifest["articles"][0]["unexpected"] = "value"
        with self.assertRaises(ValueError):
            validate_bulk_manifest(manifest)

    def test_unknown_top_level_fields_are_rejected(self):
        manifest = self.valid_manifest()
        manifest["unexpected"] = "value"
        with self.assertRaises(ValueError):
            validate_bulk_manifest(manifest)

    def test_delete_queue_state_is_not_importable(self):
        manifest = self.valid_manifest()
        manifest["articles"][0]["status"] = SuggestedArticle.Status.DELETE_QUEUED
        with self.assertRaises(ValueError):
            validate_bulk_manifest(manifest)

    def test_pending_fields_require_matching_workflow_state(self):
        manifest = self.valid_manifest()
        manifest["articles"][0]["pending_update_body"] = "hidden update"
        with self.assertRaises(ValueError):
            validate_bulk_manifest(manifest)

    def test_review_history_structure_is_bounded(self):
        manifest = self.valid_manifest()
        manifest["articles"][0]["review_notes_history"] = [{"note": "ok", "unknown": True}]
        with self.assertRaises(ValueError):
            validate_bulk_manifest(manifest)

    def test_keyword_aliases_reject_non_text_structures(self):
        manifest = self.valid_manifest()
        manifest["articles"][0]["keywords"] = {"unexpected": "mapping"}
        with self.assertRaises(ValueError):
            validate_bulk_manifest(manifest)

    def test_failed_workflow_requires_review_comments(self):
        manifest = self.valid_manifest()
        manifest["articles"][0]["status"] = SuggestedArticle.Status.FAILED
        with self.assertRaises(ValueError):
            validate_bulk_manifest(manifest)

    def test_duplicate_json_keys_are_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "manifest.json",
                '{"format":"djopenkb-bulk-export-v1","format":"other"}',
            )
        output.seek(0)
        with zipfile.ZipFile(output) as archive:
            with self.assertRaises(ValueError):
                _read_bulk_manifest(archive, "manifest.json")

    def test_split_manifest_rejects_unknown_part_fields(self):
        manifest = {
            "format": "djopenkb-bulk-export-split-v1",
            "part_count": 1,
            "parts": [{"filename": "parts/part1.zip", "unexpected": True}],
        }
        with self.assertRaises(ValueError):
            validate_split_manifest(manifest, {"parts/part1.zip": "parts/part1.zip"})
