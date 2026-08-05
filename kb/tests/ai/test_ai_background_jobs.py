from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from kb.admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from kb.middleware import ForceLoginAndAdminGuardMiddleware


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

        from kb.views.ai_jobs import enqueue_openkb_ai_job, get_openkb_ai_job_response

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

        from kb.views.ai_jobs import (
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

        from kb.views.ai_jobs import (
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
