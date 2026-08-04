from unittest.mock import patch

from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from kb.views.services import validate_article_image_upload


class ArticleImageDecompressionBombTests(SimpleTestCase):
    def test_decompression_bomb_is_reported_as_dimension_validation_error(self):
        upload = SimpleUploadedFile("large.png", b"not-used", content_type="image/png")
        with patch(
            "kb.views.services.Image.open",
            side_effect=Image.DecompressionBombError("too many pixels"),
        ):
            with self.assertRaises(ValidationError) as context:
                validate_article_image_upload(upload)

        self.assertIn("dimensions are too large", context.exception.messages[0].lower())
