from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ArticleContentLayoutTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.templates_dir = cls.base_dir / "website" / "templates"
        cls.stylesheet = (
            cls.base_dir / "website" / "static" / "stylesheets" / "article-content.css"
        )

    def test_published_article_and_editor_previews_use_shared_content_class(self):
        published = (self.templates_dir / "articles.html").read_text(encoding="utf-8")
        create = (self.templates_dir / "suggest.html").read_text(encoding="utf-8")
        edit = (self.templates_dir / "suggest_edit.html").read_text(encoding="utf-8")

        self.assertIn('class="body_text article-rendered-content"', published)
        self.assertIn(
            'class="form-control suggest-preview article-rendered-content"',
            create,
        )
        self.assertIn(
            'class="form-control suggest-preview article-rendered-content"',
            edit,
        )

        for template in (published, create, edit):
            self.assertIn("stylesheets/article-content.css", template)

    def test_article_layout_uses_wide_shell_and_flexible_sidebar(self):
        published = (self.templates_dir / "articles.html").read_text(encoding="utf-8")

        self.assertIn('class="col-xs-12 article-detail-page-shell"', published)
        self.assertIn('class="row article-detail-layout"', published)
        self.assertIn('class="col-xs-12 article-detail-sidebar"', published)
        self.assertIn('class="col-xs-12 article-detail-main"', published)

    def test_shared_styles_wrap_prose_and_contain_wide_elements(self):
        css = self.stylesheet.read_text(encoding="utf-8")

        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn(".article-rendered-content pre", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn(".article-rendered-content table", css)
        self.assertIn(".suggest-preview.article-rendered-content", css)


    def test_editor_pages_use_wide_shell_and_scrollable_preview_canvas(self):
        javascript = (
            self.base_dir / "website" / "static" / "javascripts" / "openKB.js"
        ).read_text(encoding="utf-8")

        for template_name in ("suggest.html", "suggest_edit.html"):
            template = (self.templates_dir / template_name).read_text(encoding="utf-8")
            self.assertIn('class="col-xs-12 article-editor-page-shell"', template)
            self.assertIn('class="article-preview-canvas"', template)

        self.assertIn("lineWrapping: false", javascript)
        self.assertIn("setOption('lineWrapping', false)", javascript)
        self.assertIn("#preview .article-preview-canvas", javascript)

    def test_wide_layout_css_uses_remaining_article_width_and_horizontal_scroll(self):
        css = self.stylesheet.read_text(encoding="utf-8")

        self.assertIn(".article-detail-page-shell", css)
        self.assertIn(".article-detail-sidebar", css)
        self.assertIn("flex: 0 0 300px", css)
        self.assertIn(".article-preview-canvas", css)
        self.assertIn("width: calc(100vw - 390px)", css)
        self.assertIn(".article-editor-page-shell .CodeMirror-scroll", css)

    def test_preview_has_no_preview_only_image_height_cap(self):
        for template_name in ("suggest.html", "suggest_edit.html"):
            template = (self.templates_dir / template_name).read_text(encoding="utf-8")
            self.assertNotIn(".suggest-preview img:not([width])", template)
            self.assertNotIn("max-height: 520px", template)
