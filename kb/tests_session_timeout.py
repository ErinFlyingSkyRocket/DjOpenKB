from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .middleware import SESSION_STARTED_AT_KEY, SessionTimeoutMiddleware
from .models import SiteSetting


class SessionTimeoutHoursTests(TestCase):
    def setUp(self):
        self.site_setting = SiteSetting.load()
        self.site_setting.session_timeout_hours = 8
        self.site_setting.save(update_fields=["session_timeout_hours"])
        self.factory = RequestFactory()

    def _authenticated_request(self):
        user = get_user_model().objects.create_user(
            username="session-timeout-user",
            password="test-password",
        )
        request = self.factory.get("/home/")
        SessionMiddleware(lambda req: HttpResponse()).process_request(request)
        request.session.save()
        MessageMiddleware(lambda req: HttpResponse()).process_request(request)
        request.user = user
        return request

    def test_default_site_setting_is_eight_hours(self):
        self.assertEqual(self.site_setting.session_timeout_hours, 8)

    def test_fixed_eight_hour_expiry_redirects_to_login(self):
        request = self._authenticated_request()
        request.session[SESSION_STARTED_AT_KEY] = (
            timezone.now() - timedelta(hours=8, seconds=1)
        ).isoformat()
        request.session.save()

        response = SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/login/")

    def test_cookie_expiry_uses_remaining_time_not_a_new_eight_hours(self):
        request = self._authenticated_request()
        request.session[SESSION_STARTED_AT_KEY] = (
            timezone.now() - timedelta(hours=2)
        ).isoformat()
        request.session.save()

        response = SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)

        self.assertEqual(response.status_code, 200)
        # A small timing allowance avoids a flaky assertion around the current second.
        self.assertGreater(request.session.get_expiry_age(), (6 * 60 * 60) - 10)
        self.assertLessEqual(request.session.get_expiry_age(), 6 * 60 * 60)
