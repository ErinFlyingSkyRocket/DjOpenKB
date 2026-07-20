from django.test import SimpleTestCase

from kb.views.services import (
    extract_vimeo_video_id,
    extract_youtube_video_id,
    is_safe_direct_video_url,
    render_safe_markdown,
)


class ArticleVideoEmbedTests(SimpleTestCase):
    def test_supported_youtube_links_extract_the_same_video_id(self):
        video_id = "dQw4w9WgXcQ"
        urls = [
            f"https://www.youtube.com/watch?v={video_id}",
            f"https://youtu.be/{video_id}",
            f"https://www.youtube.com/shorts/{video_id}",
            f"https://www.youtube.com/live/{video_id}",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(extract_youtube_video_id(url), video_id)

    def test_standalone_youtube_link_renders_privacy_enhanced_iframe(self):
        rendered = render_safe_markdown(
            "Before\n\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n\nAfter"
        )

        self.assertIn('class="article-video-embed"', rendered)
        self.assertIn(
            'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"',
            rendered,
        )
        self.assertNotIn('src="https://www.youtube.com/', rendered)

    def test_supported_vimeo_links_extract_the_same_video_id(self):
        video_id = "76979871"
        urls = [
            f"https://vimeo.com/{video_id}",
            f"https://www.vimeo.com/{video_id}",
            f"https://player.vimeo.com/video/{video_id}",
            f"https://vimeo.com/channels/staffpicks/{video_id}",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(extract_vimeo_video_id(url), video_id)

    def test_standalone_vimeo_link_renders_vimeo_player(self):
        rendered = render_safe_markdown("https://vimeo.com/76979871")

        self.assertIn('class="article-video-embed"', rendered)
        self.assertIn(
            'src="https://player.vimeo.com/video/76979871"',
            rendered,
        )
        self.assertIn('title="Vimeo video player"', rendered)

    def test_direct_https_video_file_is_not_auto_embedded(self):
        video_url = "https://cdn.example.com/training/setup-guide.mp4?version=2"
        rendered = render_safe_markdown(video_url)

        # The URL shape may be a valid direct media URL, but direct media is not
        # embedded because it could trigger an external authentication challenge.
        self.assertTrue(is_safe_direct_video_url(video_url))
        self.assertNotIn("<video", rendered)
        self.assertNotIn("<iframe", rendered)
        self.assertIn(video_url, rendered)

    def test_direct_http_video_file_is_not_auto_embedded(self):
        video_url = "http://cdn.example.com/training/setup-guide.mp4"
        rendered = render_safe_markdown(video_url)

        self.assertFalse(is_safe_direct_video_url(video_url))
        self.assertNotIn("<video", rendered)
        self.assertIn(video_url, rendered)

    def test_unsupported_link_is_not_converted_to_player(self):
        rendered = render_safe_markdown("https://example.com/video/12345")

        self.assertNotIn("<iframe", rendered)
        self.assertNotIn("<video", rendered)
        self.assertIn("https://example.com/video/12345", rendered)

    def test_video_link_inside_fenced_code_is_not_embedded(self):
        rendered = render_safe_markdown(
            "```text\nhttps://vimeo.com/76979871\n```"
        )

        self.assertNotIn("<iframe", rendered)
        self.assertNotIn("<video", rendered)
        self.assertIn("vimeo.com/76979871", rendered)

    def test_arbitrary_iframe_source_is_removed(self):
        rendered = render_safe_markdown(
            '<iframe class="article-video-embed" src="https://example.com/embed/12345"></iframe>'
        )

        self.assertNotIn("https://example.com", rendered)

    def test_arbitrary_video_source_is_removed(self):
        rendered = render_safe_markdown(
            '<video class="article-video" controls src="https://example.com/not-a-video.txt"></video>'
        )

        self.assertNotIn("https://example.com/not-a-video.txt", rendered)
