from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from .admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from .middleware import ForceLoginAndAdminGuardMiddleware


@override_settings(ADMIN_MFA_IDLE_TIMEOUT_SECONDS=600)
class AdminStepUpRouteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_superuser(
            username="admin-step-up-test",
            email="admin-step-up-test@example.invalid",
            password="safe-test-password",
        )

    def _request(self, path):
        request = self.factory.get(path)
        SessionMiddleware(lambda req: HttpResponse("ok")).process_request(request)
        request.session.save()
        request.user = self.user
        return request

    def test_only_full_maintenance_routes_are_step_up_protected(self):
        self.assertTrue(is_admin_step_up_path(reverse("admin:index")))
        self.assertTrue(is_admin_step_up_path(reverse("export_articles_zip")))
        self.assertTrue(is_admin_step_up_path(reverse("manage_article_deletion_queue")))
        self.assertFalse(is_admin_step_up_path(reverse("manage_pending_articles")))
        self.assertFalse(is_admin_step_up_path(reverse("manage_internal_pending_articles")))

    def test_custom_admin_tool_requires_step_up_mfa(self):
        path = reverse("export_articles_zip")
        response = AdminMFASessionMiddleware(lambda request: HttpResponse("ok"))(self._request(path))

        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response.url)
        self.assertEqual(parsed.path, reverse("admin_mfa_verify"))
        self.assertEqual(parse_qs(parsed.query).get("next"), [path])

    def test_force_guard_also_blocks_custom_admin_tool_without_step_up_mfa(self):
        path = reverse("export_articles_zip")
        response = ForceLoginAndAdminGuardMiddleware(lambda request: HttpResponse("ok"))(self._request(path))

        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response.url)
        self.assertEqual(parsed.path, reverse("admin_mfa_verify"))
        self.assertEqual(parse_qs(parsed.query).get("next"), [path])


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class UserAdminResetActionTests(TestCase):
    """Regression coverage for the per-user Admin MFA/lockout reset buttons."""

    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-reset-test",
            email="admin-reset-test@example.invalid",
            password="safe-test-password",
        )
        self.target_user = get_user_model().objects.create_user(
            username="target-reset-test",
            email="target-reset-test@example.invalid",
            password="safe-test-password",
        )

        from django.utils import timezone

        from .admin_security import (
            ADMIN_MFA_LAST_ACTIVITY_AT_KEY,
            ADMIN_MFA_USER_ID_KEY,
            ADMIN_MFA_VERIFIED_AT_KEY,
            ADMIN_MFA_VERIFIED_KEY,
        )
        from .mfa import MFA_SESSION_KEY, MFA_USER_SESSION_KEY, get_or_create_mfa_device

        self.target_device = get_or_create_mfa_device(self.target_user)
        self.target_device.confirmed = True
        self.target_device.save(update_fields=["confirmed"])

        self.client.force_login(self.admin_user)
        session = self.client.session
        now = int(timezone.now().timestamp())
        session[MFA_SESSION_KEY] = True
        session[MFA_USER_SESSION_KEY] = str(self.admin_user.pk)
        session[ADMIN_MFA_VERIFIED_KEY] = True
        session[ADMIN_MFA_USER_ID_KEY] = str(self.admin_user.pk)
        session[ADMIN_MFA_VERIFIED_AT_KEY] = now
        session[ADMIN_MFA_LAST_ACTIVITY_AT_KEY] = now
        session.save()

    def _admin_request_kwargs(self):
        return {"REMOTE_ADDR": "127.0.0.1"}

    def test_user_admin_mfa_reset_confirmation_and_submit_work(self):
        url = reverse("admin:kb_user_reset_mfa", args=[self.target_user.pk])

        confirmation = self.client.get(url, **self._admin_request_kwargs())
        self.assertEqual(confirmation.status_code, 200)

        response = self.client.post(url, **self._admin_request_kwargs())
        self.assertRedirects(
            response,
            reverse("admin:auth_user_change", args=[self.target_user.pk]),
            fetch_redirect_response=False,
        )

        self.target_device.refresh_from_db()
        self.assertFalse(self.target_device.confirmed)
        self.assertIsNotNone(self.target_device.reset_at)

    def test_user_admin_lockout_reset_confirmation_and_submit_work(self):
        url = reverse("admin:kb_user_reset_auth_lockout", args=[self.target_user.pk])

        confirmation = self.client.get(url, **self._admin_request_kwargs())
        self.assertEqual(confirmation.status_code, 200)

        response = self.client.post(url, **self._admin_request_kwargs())
        self.assertRedirects(
            response,
            reverse("admin:auth_user_change", args=[self.target_user.pk]),
            fetch_redirect_response=False,
        )


@override_settings(
    OPENKB_AI_JOB_TTL_SECONDS=1800,
    OPENKB_AI_CELERY_BROKER_URL="memory://",
    CELERY_TASK_ALWAYS_EAGER=True,
)
class OpenKBAIBackgroundJobTests(TestCase):
    """Regression coverage for AI work that continues after page navigation."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="ai-background-test",
            email="ai-background-test@example.invalid",
            password="safe-test-password",
        )

    def test_enqueued_record_encrypts_prompt_and_returns_only_to_owner(self):
        from unittest.mock import patch
        from django.core.cache import cache

        from .views.ai_jobs import enqueue_openkb_ai_job, get_openkb_ai_job_response

        secret_question = "internal phrase that must not be plain text in Redis"
        with patch("kb.tasks.run_openkb_ai_job.apply_async") as apply_async:
            queued = enqueue_openkb_ai_job(
                user=self.user,
                question=secret_question,
                include_internal=False,
                language_code="en",
            )

        self.assertEqual(queued["status"], "queued")
        apply_async.assert_called_once()
        cached = cache.get("openkb_ai:job:" + queued["job_id"])
        self.assertIsInstance(cached, dict)
        self.assertNotIn(secret_question, str(cached))
        self.assertIn("question_encrypted", cached)

        self.assertIsNotNone(get_openkb_ai_job_response(queued["job_id"], self.user))
        other_user = get_user_model().objects.create_user(
            username="ai-background-other",
            email="ai-background-other@example.invalid",
            password="safe-test-password",
        )
        self.assertIsNone(get_openkb_ai_job_response(queued["job_id"], other_user))

    def test_worker_completion_returns_encrypted_result_and_clears_through_owner_status(self):
        from unittest.mock import patch
        from django.core.cache import cache

        from .views.ai_jobs import (
            enqueue_openkb_ai_job,
            execute_openkb_ai_job,
            get_openkb_ai_job_response,
        )

        with patch("kb.tasks.run_openkb_ai_job.apply_async"):
            queued = enqueue_openkb_ai_job(
                user=self.user,
                question="hello",
                include_internal=False,
                language_code="en",
            )

        with patch("kb.views.ai_jobs.is_openkb_small_talk_request", return_value=True), patch(
            "kb.views.ai_jobs.build_openkb_small_talk_answer", return_value="Hello from the background worker."
        ):
            execute_openkb_ai_job(queued["job_id"])

        cached = cache.get("openkb_ai:job:" + queued["job_id"])
        self.assertEqual(cached["status"], "completed")
        self.assertNotIn("Hello from the background worker.", str(cached))

        payload = get_openkb_ai_job_response(queued["job_id"], self.user)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["answer"], "Hello from the background worker.")

    def test_cancelled_job_discards_late_worker_result(self):
        from unittest.mock import patch

        from .views.ai_jobs import (
            cancel_openkb_ai_job,
            enqueue_openkb_ai_job,
            execute_openkb_ai_job,
            get_openkb_ai_job_response,
        )

        with patch("kb.tasks.run_openkb_ai_job.apply_async"):
            queued = enqueue_openkb_ai_job(
                user=self.user,
                question="hello",
                include_internal=False,
                language_code="en",
            )

        self.assertTrue(cancel_openkb_ai_job(queued["job_id"], self.user))
        with patch("kb.views.ai_jobs.is_openkb_small_talk_request", return_value=True), patch(
            "kb.views.ai_jobs.build_openkb_small_talk_answer", return_value="Late answer"
        ):
            execute_openkb_ai_job(queued["job_id"])

        payload = get_openkb_ai_job_response(queued["job_id"], self.user)
        self.assertEqual(payload["status"], "cancelled")
        self.assertNotIn("answer", payload)
