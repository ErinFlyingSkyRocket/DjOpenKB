import io
import json
import zipfile
from unittest.mock import patch

from django.test import SimpleTestCase

from kb.views.services_bulk import _preflight_bulk_import_archive


class BulkImportResourceLimitTests(SimpleTestCase):
    @staticmethod
    def _zip_bytes(files):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return output.getvalue()

    def test_cumulative_uncompressed_budget_is_enforced(self):
        archive_bytes = self._zip_bytes({"article.md": "x" * 100})
        with patch(
            "kb.views.services_bulk.BULK_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES",
            50,
        ):
            with self.assertRaisesMessage(ValueError, "cumulative uncompressed size"):
                _preflight_bulk_import_archive(io.BytesIO(archive_bytes))

    def test_nested_split_packages_are_rejected(self):
        leaf_part = self._zip_bytes({"article.md": "# Article\n\nBody"})
        nested_manifest = {
            "format": "djopenkb-bulk-export-split-v1",
            "parts": [{"filename": "leaf.zip"}],
        }
        nested_split = self._zip_bytes(
            {
                "manifest.json": json.dumps(nested_manifest),
                "leaf.zip": leaf_part,
            }
        )
        outer_manifest = {
            "format": "djopenkb-bulk-export-split-v1",
            "parts": [{"filename": "nested.zip"}],
        }
        outer = self._zip_bytes(
            {
                "manifest.json": json.dumps(outer_manifest),
                "nested.zip": nested_split,
            }
        )

        with self.assertRaisesMessage(ValueError, "Nested split import packages"):
            _preflight_bulk_import_archive(io.BytesIO(outer))

    def test_split_part_count_is_limited(self):
        manifest = {
            "format": "djopenkb-bulk-export-split-v1",
            "parts": [{"filename": f"part-{index}.zip"} for index in range(3)],
        }
        files = {"manifest.json": json.dumps(manifest)}
        for index in range(3):
            files[f"part-{index}.zip"] = self._zip_bytes({"article.md": "# A"})
        outer = self._zip_bytes(files)

        with patch("kb.views.services_bulk.BULK_IMPORT_MAX_PART_ARCHIVES", 2):
            with self.assertRaisesMessage(ValueError, "too many part archives"):
                _preflight_bulk_import_archive(io.BytesIO(outer))
