from unittest.mock import patch

from django.test import SimpleTestCase

from kb.views.services import (
    _perform_microsoft_video_access_probe,
    check_microsoft_video_anonymous_access,
    is_microsoft_cloud_video_url,
    standalone_article_video_urls,
    validate_article_video_links_for_anonymous_access,
)


class _FakeResponse:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}
        self.closed = False

    def getcode(self):
        return self.status

    def close(self):
        self.closed = True


class _FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("Unexpected extra video access probe")
        return self.responses.pop(0)


class ArticleVideoAnonymousAccessTests(SimpleTestCase):
    def test_recognises_sharepoint_and_onedrive_direct_video_urls(self):
        self.assertTrue(
            is_microsoft_cloud_video_url(
                "https://tenant.sharepoint.com/sites/help/Videos/setup.mp4"
            )
        )
        self.assertTrue(
            is_microsoft_cloud_video_url(
                "https://public.dm.files.1drv.com/video/example.webm"
            )
        )
        self.assertFalse(
            is_microsoft_cloud_video_url("https://example.com/video.mp4")
        )

    @patch("kb.views.services.build_opener")
    def test_rejects_external_authentication_challenge(self, build_opener_mock):
        build_opener_mock.return_value = _FakeOpener([
            _FakeResponse(401, {"WWW-Authenticate": 'Basic realm="SharePoint"'})
        ])
        result = _perform_microsoft_video_access_probe(
            "https://tenant.sharepoint.com/sites/help/Videos/private.mp4"
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "auth_required")

    @patch("kb.views.services.build_opener")
    def test_rejects_redirect_to_microsoft_login(self, build_opener_mock):
        build_opener_mock.return_value = _FakeOpener([
            _FakeResponse(302, {"Location": "https://login.microsoftonline.com/common/oauth2/authorize"})
        ])
        result = _perform_microsoft_video_access_probe(
            "https://tenant.sharepoint.com/sites/help/Videos/private.mp4"
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "auth_required")

    @patch("kb.views.services.build_opener")
    def test_accepts_anonymously_accessible_direct_video(self, build_opener_mock):
        build_opener_mock.return_value = _FakeOpener([
            _FakeResponse(206, {"Content-Type": "video/mp4"})
        ])
        result = _perform_microsoft_video_access_probe(
            "https://tenant.sharepoint.com/sites/help/Videos/public.mp4"
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "accessible")

    def test_direct_microsoft_video_is_not_treated_as_embeddable_video(self):
        markdown = """Visible video:\nhttps://tenant.sharepoint.com/sites/help/Videos/public.mp4\n\n```\nhttps://tenant.sharepoint.com/sites/help/Videos/private.mp4\n```\n"""
        self.assertEqual(standalone_article_video_urls(markdown), [])

    @patch("kb.views.services.check_microsoft_video_anonymous_access")
    def test_article_validation_does_not_probe_non_embeddable_direct_video(self, check_mock):
        markdown = "https://tenant.sharepoint.com/sites/help/Videos/private.mp4"
        validate_article_video_links_for_anonymous_access(markdown)
        check_mock.assert_not_called()

    @patch("kb.views.services._perform_microsoft_video_access_probe")
    def test_access_results_are_cached_briefly(self, probe_mock):
        probe_mock.return_value = {"allowed": True, "reason": "accessible", "status": 206}
        url = "https://tenant.sharepoint.com/sites/help/Videos/cache-test-unique.mp4"
        first = check_microsoft_video_anonymous_access(url)
        second = check_microsoft_video_anonymous_access(url)
        self.assertTrue(first["allowed"])
        self.assertEqual(first, second)
        probe_mock.assert_called_once_with(url)
