from django.test import TestCase
from django.urls import reverse

from kb.models import SuggestedArticle
from kb.permissions import ROLE_ADMIN_USERS, ROLE_ARTICLE_MANAGER, ROLE_ARTICLE_WRITER, ROLE_REGULAR_USER

from .helpers import DjOpenKBTestMixin


class RouteAuthorizationByRoleTests(DjOpenKBTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.regular = self.make_user("route_regular", role=ROLE_REGULAR_USER)
        self.writer = self.make_user("route_writer", role=ROLE_ARTICLE_WRITER)
        self.manager = self.make_user("route_manager", role=ROLE_ARTICLE_MANAGER)
        self.admin = self.make_user("route_admin", role=ROLE_ADMIN_USERS)
        self.published = self.make_article("Route Published Article", owner=self.writer, status=SuggestedArticle.Status.PUBLISHED)
        self.pending = self.make_article("Route Pending Article", owner=self.writer, status=SuggestedArticle.Status.PENDING)

    def test_regular_user_routes(self):
        client = self.login_client(self.regular)
        self.assertEqual(client.get(reverse("home")).status_code, 200)
        self.assertEqual(client.get(reverse("article_detail", kwargs={"article_id": self.published.pk})).status_code, 200)
        self.assertEqual(client.get(reverse("suggest")).status_code, 404)
        self.assertEqual(client.get(reverse("edit_my_suggestions")).status_code, 404)
        self.assertEqual(client.get(reverse("manage_pending_articles")).status_code, 404)
        self.assertEqual(client.get(reverse("admin_bulk_articles")).status_code, 404)

    def test_writer_routes(self):
        client = self.login_client(self.writer)
        self.assertEqual(client.get(reverse("home")).status_code, 200)
        self.assertEqual(client.get(reverse("suggest")).status_code, 200)
        self.assertEqual(client.get(reverse("edit_my_suggestions")).status_code, 200)
        self.assertEqual(client.get(reverse("manage_pending_articles")).status_code, 404)
        self.assertEqual(client.get(reverse("admin_bulk_articles")).status_code, 404)

    def test_article_manager_routes(self):
        client = self.login_client(self.manager)
        self.assertEqual(client.get(reverse("home")).status_code, 200)
        self.assertEqual(client.get(reverse("suggest")).status_code, 404)
        self.assertEqual(client.get(reverse("manage_pending_articles")).status_code, 200)
        self.assertEqual(client.get(reverse("edit_suggestion", kwargs={"article_id": self.pending.pk})).status_code, 200)
        self.assertEqual(client.get(reverse("admin_bulk_articles")).status_code, 404)

    def test_admin_routes(self):
        client = self.login_client(self.admin)
        self.assertEqual(client.get(reverse("home")).status_code, 200)
        self.assertEqual(client.get(reverse("suggest")).status_code, 200)
        self.assertEqual(client.get(reverse("manage_pending_articles")).status_code, 200)
        self.assertEqual(client.get(reverse("admin_bulk_articles")).status_code, 200)
        self.assertEqual(client.get(reverse("manage_orphan_articles")).status_code, 200)
        self.assertEqual(client.get(reverse("clean_stray_upload_files")).status_code, 200)

    def test_post_only_endpoints_reject_get(self):
        client = self.login_client(self.writer)
        self.assertEqual(client.get(reverse("vote_article", kwargs={"article_id": self.published.pk})).status_code, 405)
        self.assertEqual(client.get(reverse("ask_openkb_ai")).status_code, 405)
        self.assertEqual(client.get(reverse("upload_article_image")).status_code, 405)
