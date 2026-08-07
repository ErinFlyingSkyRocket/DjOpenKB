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

        self.assertIn("lineWrapping: true", javascript)
        self.assertIn("setOption('lineWrapping', true)", javascript)
        self.assertIn("#preview .article-preview-canvas", javascript)

    def test_preview_matches_published_width_while_editor_soft_wraps_comfortably(self):
        css = self.stylesheet.read_text(encoding="utf-8")

        self.assertIn(".article-detail-page-shell", css)
        self.assertIn(".article-detail-sidebar", css)
        self.assertIn("--article-sidebar-width: 280px", css)
        self.assertIn(".article-preview-canvas", css)
        self.assertIn("--article-canvas-max-width: 1480px", css)
        self.assertIn("width: var(--article-shared-canvas-width) !important", css)
        self.assertIn(".article-editor-page-shell .CodeMirror-scroll", css)
        self.assertIn("overflow-x: hidden !important", css)
        self.assertIn("overflow-y: scroll !important", css)
        self.assertIn(".article-editor-page-shell .CodeMirror-hscrollbar", css)
        self.assertIn("display: none !important", css)
        self.assertIn("white-space: pre-wrap", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("ui-monospace", css)
        self.assertNotIn(
            "calc(var(--article-shared-canvas-width) + 30px)",
            css,
        )

        javascript = (
            self.base_dir / "website" / "static" / "javascripts" / "openKB.js"
        ).read_text(encoding="utf-8")
        self.assertIn("lineWrapping: true", javascript)
        self.assertIn("setOption('lineWrapping', true)", javascript)
        self.assertNotIn("equivalentHorizontalOffset", javascript)
        self.assertNotIn("syncPreviewHorizontalFromEditor", javascript)
        self.assertNotIn("syncEditorHorizontalFromPreview", javascript)
        self.assertNotIn("syncHorizontalAfterPreviewRender", javascript)

    def test_preview_has_no_preview_only_image_height_cap(self):
        for template_name in ("suggest.html", "suggest_edit.html"):
            template = (self.templates_dir / template_name).read_text(encoding="utf-8")
            self.assertNotIn(".suggest-preview img:not([width])", template)
            self.assertNotIn("max-height: 520px", template)

    def test_video_preview_and_published_article_share_admin_configured_width(self):
        csp_css = (
            self.base_dir / "website" / "static" / "stylesheets" / "csp-template.css"
        ).read_text(encoding="utf-8")
        javascript = (
            self.base_dir / "website" / "static" / "javascripts" / "openKB.js"
        ).read_text(encoding="utf-8")

        self.assertIn("var(--article-video-max-width, 720px)", csp_css)
        self.assertGreaterEqual(javascript.count('class="article-video-wrapper"'), 2)

        for template_name in ("articles.html", "suggest.html", "suggest_edit.html"):
            template = (self.templates_dir / template_name).read_text(encoding="utf-8")
            self.assertIn(
                "--article-video-max-width: {{ article_video_max_width_px|default:720 }}px",
                template,
            )
            self.assertIn("20260807-media-display-1", template)

    def test_image_size_dialog_offers_quarter_steps_through_two_hundred_percent(self):
        modal = (self.templates_dir / "_image_size_modal.html").read_text(encoding="utf-8")

        for percent in (25, 50, 75, 100, 125, 150, 175, 200):
            self.assertIn(
                f'data-image-size-percent="{percent}">{percent}%</button>',
                modal,
            )

        self.assertIn('id="imageSizeWidth"', modal)
        self.assertIn('id="imageSizeHeight"', modal)

