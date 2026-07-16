import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class ContentSecurityPolicyTests(TestCase):
    def test_login_response_uses_nonce_csp_without_unsafe_inline(self):
        response = self.client.get(reverse("login"))
        policy = response["Content-Security-Policy"]

        self.assertNotIn("unsafe-inline", policy)
        self.assertIn("script-src-attr 'none'", policy)
        self.assertIn("style-src-attr 'none'", policy)
        self.assertIn("frame-src https://www.youtube-nocookie.com https://player.vimeo.com", policy)
        self.assertIn("media-src 'self' https:", policy)
        self.assertNotIn("frame-src 'none'", policy)

        match = re.search(r"script-src 'self' 'nonce-([^']+)'", policy)
        self.assertIsNotNone(match)
        self.assertIn(
            f'nonce="{match.group(1)}"',
            response.content.decode("utf-8"),
        )

    def test_project_templates_have_no_inline_event_or_style_attributes(self):
        templates_dir = Path(settings.BASE_DIR) / "website" / "templates"
        event_attribute = re.compile(r"\son[a-zA-Z0-9_-]+\s*=", re.IGNORECASE)
        style_attribute = re.compile(r"\sstyle\s*=", re.IGNORECASE)

        for template in templates_dir.rglob("*.html"):
            content = template.read_text(encoding="utf-8")
            self.assertIsNone(event_attribute.search(content), template)
            self.assertIsNone(style_attribute.search(content), template)

    def test_project_generated_admin_html_has_no_inline_style_attributes(self):
        for source_name in ("admin.py", "permissions.py"):
            source = Path(settings.BASE_DIR) / "kb" / source_name
            self.assertNotIn("style=", source.read_text(encoding="utf-8"), source)
