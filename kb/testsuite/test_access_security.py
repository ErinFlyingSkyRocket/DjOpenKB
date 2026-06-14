from django.test import TestCase
from django.urls import reverse

from kb.mfa import clear_mfa_verified
from kb.models import UserProfile
from kb.permissions import ROLE_ADMIN_USERS, ROLE_REGULAR_USER

from .helpers import DjOpenKBTestMixin


class AnonymousAccessSecurityTests(DjOpenKBTestMixin, TestCase):
    def test_root_and_login_are_public_entry_points(self):
        self.assertEqual(self.client.get(reverse("root_login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_anonymous_protected_pages_return_404(self):
        article = self.make_article("Published Article For Anonymous Test")
        protected_urls = [
            reverse("home"),
            reverse("search") + "?q=published",
            reverse("search_article_suggestions") + "?q=pu",
            reverse("suggest"),
            reverse("edit_my_suggestions"),
            reverse("admin_bulk_articles"),
            reverse("manage_pending_articles"),
            reverse("article_detail", kwargs={"article_id": article.pk}),
            reverse("vote_article", kwargs={"article_id": article.pk}),
            reverse("ask_openkb_ai"),
            "/admin/login/",
        ]
        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)

    def test_logged_in_user_without_mfa_is_redirected_to_mfa(self):
        user = self.make_user("needs_mfa", role=ROLE_REGULAR_USER)
        client = self.client
        client.force_login(user, backend="kb.backends.EmailOrUsernameModelBackend")
        clear_mfa_verified(type("Req", (), {"session": client.session})())
        response = client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("mfa_verify"), response.url)

    def test_logged_in_user_with_mfa_can_load_home(self):
        user = self.make_user("regular_home", role=ROLE_REGULAR_USER)
        client = self.login_client(user)
        self.assertEqual(client.get(reverse("home")).status_code, 200)

    def test_user_with_main_site_access_disabled_cannot_enter_site(self):
        user = self.make_user("blocked_user", role=ROLE_REGULAR_USER, can_access_main_site=False)
        client = self.login_client(user)
        response = client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("home"), response.url)


class AdminGuardMiddlewareTests(DjOpenKBTestMixin, TestCase):
    def test_admin_login_url_is_hidden_even_for_anonymous(self):
        self.assertEqual(self.client.get("/admin/login/").status_code, 404)

    def test_regular_user_cannot_access_django_admin(self):
        regular = self.make_user("not_staff", role=ROLE_REGULAR_USER)
        client = self.login_client(regular)
        self.assertEqual(client.get("/admin/").status_code, 404)

    def test_admin_group_user_can_access_django_admin_from_allowed_ip(self):
        admin = self.make_user("role_admin", role=ROLE_ADMIN_USERS)
        client = self.login_client(admin)
        response = client.get("/admin/", REMOTE_ADDR="127.0.0.1")
        self.assertNotEqual(response.status_code, 404)

    def test_admin_group_user_from_disallowed_ip_gets_404(self):
        admin = self.make_user("role_admin_bad_ip", role=ROLE_ADMIN_USERS)
        client = self.login_client(admin)
        response = client.get("/admin/", REMOTE_ADDR="203.0.113.10")
        self.assertEqual(response.status_code, 404)
