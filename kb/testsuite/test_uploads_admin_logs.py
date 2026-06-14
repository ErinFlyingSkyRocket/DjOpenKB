from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from kb.models import ActivityLog, AuthActivityLog, SuggestedArticle
from kb.permissions import ROLE_ADMIN_USERS, ROLE_ARTICLE_MANAGER, ROLE_ARTICLE_WRITER, ROLE_REGULAR_USER

from .helpers import DjOpenKBTestMixin, VALID_TINY_PNG_BYTES


class UploadEndpointSecurityTests(DjOpenKBTestMixin, TestCase):
    def test_regular_user_cannot_upload_article_images(self):
        regular = self.make_user("upload_regular", role=ROLE_REGULAR_USER)
        client = self.login_client(regular)
        uploaded = SimpleUploadedFile("test.png", VALID_TINY_PNG_BYTES, content_type="image/png")
        response = client.post(reverse("upload_article_image"), {"image": uploaded})
        self.assertEqual(response.status_code, 404)

    def test_writer_can_upload_valid_image_and_delete_current_session_upload(self):
        writer = self.make_user("upload_writer", role=ROLE_ARTICLE_WRITER)
        client = self.login_client(writer)
        with self.temporary_openkb_paths():
            uploaded = SimpleUploadedFile("test.png", VALID_TINY_PNG_BYTES, content_type="image/png")
            response = client.post(reverse("upload_article_image"), {"image": uploaded})
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["filename"].endswith(".png"))
            self.assertTrue(payload["url"].startswith("/wiki/uploads/"))

            delete_response = client.post(reverse("delete_article_image"), {"filename": payload["filename"]})
            self.assertEqual(delete_response.status_code, 200)
            self.assertTrue(delete_response.json()["deleted"])

    def test_upload_rejects_non_image_file(self):
        writer = self.make_user("upload_bad_writer", role=ROLE_ARTICLE_WRITER)
        client = self.login_client(writer)
        with self.temporary_openkb_paths():
            uploaded = SimpleUploadedFile("not-image.png", b"not really an image", content_type="image/png")
            response = client.post(reverse("upload_article_image"), {"image": uploaded})
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.json())

    def test_delete_upload_rejects_path_traversal_filename(self):
        writer = self.make_user("upload_delete_writer", role=ROLE_ARTICLE_WRITER)
        client = self.login_client(writer)
        response = client.post(reverse("delete_article_image"), {"filename": "../secret.png"})
        self.assertEqual(response.status_code, 400)


class AdminToolAccessTests(DjOpenKBTestMixin, TestCase):
    def test_only_admin_users_can_use_full_admin_tools(self):
        writer = self.make_user("admin_tool_writer", role=ROLE_ARTICLE_WRITER)
        manager = self.make_user("admin_tool_manager", role=ROLE_ARTICLE_MANAGER)
        admin = self.make_user("admin_tool_admin", role=ROLE_ADMIN_USERS)

        for user in (writer, manager):
            client = self.login_client(user)
            with self.subTest(user=user.username):
                self.assertEqual(client.get(reverse("admin_bulk_articles")).status_code, 404)
                self.assertEqual(client.get(reverse("manage_orphan_articles")).status_code, 404)
                self.assertEqual(client.get(reverse("clean_stray_upload_files")).status_code, 404)

        admin_client = self.login_client(admin)
        self.assertEqual(admin_client.get(reverse("admin_bulk_articles")).status_code, 200)
        self.assertEqual(admin_client.get(reverse("manage_orphan_articles")).status_code, 200)
        self.assertEqual(admin_client.get(reverse("clean_stray_upload_files")).status_code, 200)

    def test_article_manager_can_open_pending_queue_but_not_full_admin_tools(self):
        manager = self.make_user("pending_queue_manager", role=ROLE_ARTICLE_MANAGER)
        client = self.login_client(manager)
        self.assertEqual(client.get(reverse("manage_pending_articles")).status_code, 200)
        self.assertEqual(client.get(reverse("admin_bulk_articles")).status_code, 404)


class AuditLogImmutabilityTests(DjOpenKBTestMixin, TestCase):
    def test_activity_log_is_append_only(self):
        user = self.make_user("log_user", role=ROLE_REGULAR_USER)
        log = ActivityLog.objects.create(event_type=ActivityLog.EventType.ADMIN_TOOL_ACTION, user=user, username=user.username)
        log.details = {"changed": True}
        with self.assertRaises(ValidationError):
            log.save()
        with self.assertRaises(ValidationError):
            log.delete()

    def test_auth_log_is_append_only(self):
        user = self.make_user("auth_log_user", role=ROLE_REGULAR_USER)
        log = AuthActivityLog.objects.create(event_type=AuthActivityLog.EventType.PASSWORD_SUCCESS, success=True, user=user, username=user.username)
        log.success = False
        with self.assertRaises(ValidationError):
            log.save()
        with self.assertRaises(ValidationError):
            log.delete()
