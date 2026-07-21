from django.test import SimpleTestCase

from kb.views.services import extract_article_image_filenames, render_safe_markdown


class ArticleImageMarkdownTests(SimpleTestCase):
    def test_extracts_server_generated_image_markdown(self):
        body = "Before\n![image](/wiki/uploads/20260713-abc123.png)\nAfter"

        self.assertEqual(
            extract_article_image_filenames(body),
            ["20260713-abc123.png"],
        )

    def test_keeps_image_associated_after_common_manual_markdown_edits(self):
        body = (
            "![Updated alt text](</wiki/uploads/20260713-abc123.png?preview=1> \"Diagram\")\n"
            "[![Linked image](/wiki/uploads/20260713-def456.webp#section)](/article/42)"
        )

        self.assertEqual(
            extract_article_image_filenames(body),
            ["20260713-abc123.png", "20260713-def456.webp"],
        )

    def test_ignores_external_images_and_nested_upload_paths(self):
        body = (
            "![External](https://example.com/image.png)\n"
            "![Nested](/wiki/uploads/folder/image.png)"
        )

        self.assertEqual(extract_article_image_filenames(body), [])

    def test_returns_each_uploaded_filename_once(self):
        body = (
            "![One](/wiki/uploads/20260713-abc123.png)\n"
            "![Two](/wiki/uploads/20260713-abc123.png?second=1)"
        )

        self.assertEqual(
            extract_article_image_filenames(body),
            ["20260713-abc123.png"],
        )


class ArticleImageSizingTests(SimpleTestCase):
    def test_extracts_resized_html_image_reference(self):
        body = '<img src="/wiki/uploads/20260713-abc123.png" alt="image" width="600">'

        self.assertEqual(
            extract_article_image_filenames(body),
            ["20260713-abc123.png"],
        )

    def test_safe_numeric_image_width_is_preserved(self):
        rendered = render_safe_markdown(
            '<img src="/wiki/uploads/20260713-abc123.png" alt="image" width="600" height="400">'
        )

        self.assertIn('width="600"', rendered)
        self.assertNotIn('height=', rendered)

    def test_excessive_or_non_numeric_image_width_is_removed(self):
        oversized = render_safe_markdown(
            '<img src="/wiki/uploads/20260713-abc123.png" width="1201">'
        )
        invalid = render_safe_markdown(
            '<img src="/wiki/uploads/20260713-abc123.png" width="600px">'
        )

        self.assertNotIn('width=', oversized)
        self.assertNotIn('width=', invalid)
