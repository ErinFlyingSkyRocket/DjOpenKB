from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from kb.middleware import NginxErrorPageMiddleware
from kb.views.errors import render_http_error


class FriendlyErrorPageTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, path="/missing/"):
        request = self.factory.get(path, secure=True, HTTP_HOST="testserver")
        request.user = AnonymousUser()
        return request

    def test_shared_404_page_uses_expected_layout(self):
        response = render_http_error(self._request(), 404)

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, "HTTP 404", status_code=404)
        self.assertContains(response, "stylesheets/error-page.css", status_code=404)
        self.assertEqual(response["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0, private")

    def test_nginx_429_marker_renders_friendly_rate_limit_page(self):
        request = self.factory.get(
            "/__nginx_error/429/",
            secure=True,
            HTTP_HOST="testserver",
            HTTP_X_DJOPENKB_NGINX_ERROR="429",
        )
        request.user = AnonymousUser()
        middleware = NginxErrorPageMiddleware(lambda _request: HttpResponse("normal"))

        response = middleware(request)

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many requests", status_code=429)
        self.assertContains(response, "HTTP 429", status_code=429)
        self.assertEqual(response["Retry-After"], "30")

    def test_untrusted_or_unknown_marker_is_ignored(self):
        request = self.factory.get(
            "/",
            secure=True,
            HTTP_HOST="testserver",
            HTTP_X_DJOPENKB_NGINX_ERROR="999",
        )
        request.user = AnonymousUser()
        middleware = NginxErrorPageMiddleware(lambda _request: HttpResponse("normal"))

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"normal")

    def test_external_referrer_is_not_used_for_back_link(self):
        request = self.factory.get(
            "/missing/",
            secure=True,
            HTTP_HOST="testserver",
            HTTP_REFERER="https://example.invalid/phishing",
        )
        request.user = AnonymousUser()

        response = render_http_error(request, 404)

        self.assertNotContains(response, "example.invalid", status_code=404)
        self.assertContains(response, 'href="/"', status_code=404)


@override_settings(DEBUG=False)
class ConfiguredHandlerTests(SimpleTestCase):
    def test_anonymous_missing_route_uses_friendly_404_handler(self):
        response = self.client.get("/definitely-not-a-real-page/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, "HTTP 404", status_code=404)
