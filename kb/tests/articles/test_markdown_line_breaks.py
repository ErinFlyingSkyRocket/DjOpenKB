from django.test import SimpleTestCase

from kb.views.services import render_safe_markdown


class MarkdownLineBreakRenderingTests(SimpleTestCase):
    def test_single_enter_is_rendered_as_visible_line_break(self):
        rendered = render_safe_markdown("First line\nSecond line")

        self.assertRegex(rendered, r"First line<br\s*/?>\s*Second line")

    def test_blank_line_still_creates_separate_paragraphs(self):
        rendered = render_safe_markdown("First paragraph\n\nSecond paragraph")

        self.assertIn("<p>First paragraph</p>", rendered)
        self.assertIn("<p>Second paragraph</p>", rendered)

    def test_line_breaks_remain_sanitized(self):
        rendered = render_safe_markdown("Safe line\n<script>alert(1)</script>Next line")

        self.assertRegex(rendered, r"Safe line<br\s*/?>")
        self.assertNotIn("<script", rendered)
        self.assertNotIn("</script>", rendered)
