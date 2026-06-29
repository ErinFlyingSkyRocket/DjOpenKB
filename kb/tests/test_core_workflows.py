from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from kb.admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from kb.middleware import ForceLoginAndAdminGuardMiddleware


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

        from kb.admin_security import (
            ADMIN_MFA_LAST_ACTIVITY_AT_KEY,
            ADMIN_MFA_USER_ID_KEY,
            ADMIN_MFA_VERIFIED_AT_KEY,
            ADMIN_MFA_VERIFIED_KEY,
        )
        from kb.mfa import MFA_SESSION_KEY, MFA_USER_SESSION_KEY, get_or_create_mfa_device

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



@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    REDIS_URL="",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "openkb-ai-fixed-window-tests",
        }
    },
)
class OpenKBAIFixed24HourQuotaTests(TestCase):
    """Regression coverage for the per-user first-prompt fixed 24-hour cap."""

    def setUp(self):
        from django.core.cache import cache

        from kb.models import SiteSetting
        from kb.permissions import ROLE_REGULAR_USER, assign_single_role_group

        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="ai-fixed-window-test",
            email="ai-fixed-window-test@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.user, ROLE_REGULAR_USER)
        self.client.force_login(self.user)

        setting = SiteSetting.load()
        setting.openkb_ai_prompt_limit_per_24_hours = 2
        setting.save(update_fields=["openkb_ai_prompt_limit_per_24_hours", "updated_at"])

    @property
    def _quota_key(self):
        return f"openkb_ai:quota24h:user:{self.user.pk}"

    def _ask(self, question):
        return self.client.post(reverse("ask_openkb_ai"), {"question": question})

    def test_first_prompt_starts_one_fixed_window_and_later_prompts_do_not_extend_it(self):
        from unittest.mock import patch
        from django.core.cache import cache

        with patch("kb.tasks.run_openkb_ai_job.apply_async"):
            first = self._ask("first valid question")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(cache.get(self._quota_key), 1)

            second = self._ask("second valid question")
            self.assertEqual(second.status_code, 202)
            self.assertEqual(cache.get(self._quota_key), 2)

            third = self._ask("third valid question")

        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["prompt_limit"], 2)
        self.assertEqual(third.json()["prompt_used"], 2)

    def test_invalid_prompt_does_not_start_or_consume_the_window(self):
        from unittest.mock import patch
        from django.core.cache import cache

        blank = self._ask("")
        self.assertEqual(blank.status_code, 400)
        self.assertIsNone(cache.get(self._quota_key))

        with patch("kb.tasks.run_openkb_ai_job.apply_async"):
            accepted = self._ask("first valid question")

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(cache.get(self._quota_key), 1)

    def test_admin_save_invalidates_the_cached_quota_setting(self):
        from django.core.cache import cache
        from kb.models import SiteSetting
        from kb.views.services_ai import get_openkb_ai_prompt_limit_per_24_hours

        self.assertEqual(get_openkb_ai_prompt_limit_per_24_hours(), 2)
        self.assertEqual(cache.get("openkb_ai:quota24h:configured-limit"), 2)

        setting = SiteSetting.load()
        setting.openkb_ai_prompt_limit_per_24_hours = 7
        setting.save(update_fields=["openkb_ai_prompt_limit_per_24_hours", "updated_at"])

        self.assertIsNone(cache.get("openkb_ai:quota24h:configured-limit"))
        self.assertEqual(get_openkb_ai_prompt_limit_per_24_hours(), 7)


class ManagerRolePrecedenceTests(TestCase):
    """Manager is the highest standard role within its own article scope."""

    def setUp(self):
        from kb.permissions import seed_djopenkb_role_groups

        seed_djopenkb_role_groups()
        self.user = get_user_model().objects.create_user(
            username="manager-role-precedence-test",
            email="manager-role-precedence-test@example.invalid",
            password="safe-test-password",
        )

    def _groups(self):
        return set(self.user.groups.values_list("name", flat=True))

    def test_public_manager_removes_public_writer_and_approver(self):
        from django.contrib.auth.models import Group

        from kb.permissions import (
            ROLE_ARTICLE_APPROVER,
            ROLE_ARTICLE_MANAGER,
            ROLE_ARTICLE_WRITER,
        )

        writer = Group.objects.get(name=ROLE_ARTICLE_WRITER)
        approver = Group.objects.get(name=ROLE_ARTICLE_APPROVER)
        manager = Group.objects.get(name=ROLE_ARTICLE_MANAGER)

        # Exercise the normal m2m signal path used by the Django Admin group
        # selector, not only the explicit bulk-role action.
        with self.captureOnCommitCallbacks(execute=True):
            self.user.groups.add(writer, approver, manager)

        role_names = self._groups()
        self.assertIn(ROLE_ARTICLE_MANAGER, role_names)
        self.assertNotIn(ROLE_ARTICLE_WRITER, role_names)
        self.assertNotIn(ROLE_ARTICLE_APPROVER, role_names)

    def test_internal_manager_removes_only_lower_internal_roles(self):
        from django.contrib.auth.models import Group

        from kb.permissions import (
            ROLE_ARTICLE_APPROVER,
            ROLE_INTERNAL_ARTICLE_APPROVER,
            ROLE_INTERNAL_ARTICLE_MANAGER,
            ROLE_INTERNAL_ARTICLE_WRITER,
            ROLE_INTERNAL_USER,
        )

        public_approver = Group.objects.get(name=ROLE_ARTICLE_APPROVER)
        internal_user = Group.objects.get(name=ROLE_INTERNAL_USER)
        internal_writer = Group.objects.get(name=ROLE_INTERNAL_ARTICLE_WRITER)
        internal_approver = Group.objects.get(name=ROLE_INTERNAL_ARTICLE_APPROVER)
        internal_manager = Group.objects.get(name=ROLE_INTERNAL_ARTICLE_MANAGER)

        with self.captureOnCommitCallbacks(execute=True):
            self.user.groups.add(
                public_approver,
                internal_user,
                internal_writer,
                internal_approver,
                internal_manager,
            )

        role_names = self._groups()
        self.assertIn(ROLE_ARTICLE_APPROVER, role_names)
        self.assertIn(ROLE_INTERNAL_ARTICLE_MANAGER, role_names)
        self.assertNotIn(ROLE_INTERNAL_USER, role_names)
        self.assertNotIn(ROLE_INTERNAL_ARTICLE_WRITER, role_names)
        self.assertNotIn(ROLE_INTERNAL_ARTICLE_APPROVER, role_names)

    def test_assigning_public_manager_immediately_normalises_existing_roles(self):
        from django.contrib.auth.models import Group

        from kb.permissions import (
            ROLE_ARTICLE_APPROVER,
            ROLE_ARTICLE_MANAGER,
            ROLE_ARTICLE_WRITER,
            assign_single_role_group,
        )

        self.user.groups.add(
            Group.objects.get(name=ROLE_ARTICLE_WRITER),
            Group.objects.get(name=ROLE_ARTICLE_APPROVER),
        )
        assign_single_role_group(self.user, ROLE_ARTICLE_MANAGER)

        role_names = self._groups()
        self.assertIn(ROLE_ARTICLE_MANAGER, role_names)
        self.assertNotIn(ROLE_ARTICLE_WRITER, role_names)
        self.assertNotIn(ROLE_ARTICLE_APPROVER, role_names)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class ArticleManagerApprovalWorkflowTests(TestCase):
    """Regression coverage for public manager article approval/edit transitions."""

    def setUp(self):
        from unittest.mock import patch

        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.http import HttpResponse

        from kb.permissions import ROLE_ARTICLE_MANAGER, assign_single_role_group, seed_djopenkb_role_groups

        self._patch = patch
        self._fallback_storage = FallbackStorage
        self._session_middleware = SessionMiddleware
        self._response_class = HttpResponse
        self.factory = RequestFactory()

        seed_djopenkb_role_groups()
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="article-manager-workflow-test",
            email="article-manager-workflow-test@example.invalid",
            password="safe-test-password",
        )
        assign_single_role_group(self.manager, ROLE_ARTICLE_MANAGER)
        self.owner = User.objects.create_user(
            username="article-owner-workflow-test",
            email="article-owner-workflow-test@example.invalid",
            password="safe-test-password",
        )

    def _post_review(self, article, *, status, editor_mode="review"):
        from kb.views.suggestions import edit_suggestion

        request = self.factory.post(
            reverse("edit_suggestion", args=[article.pk]),
            data={
                "frm_kb_title": article.pending_update_title or article.title,
                "frm_kb_body": article.pending_update_body or article.body,
                "frm_kb_keywords": article.pending_update_keywords or article.keywords,
                "submit_action": "save",
                "status": status,
                "editor_mode": editor_mode,
                "next": reverse("edit_my_suggestions"),
            },
        )
        request.user = self.manager
        self._session_middleware(lambda req: self._response_class("ok")).process_request(request)
        request.session.save()
        setattr(request, "_messages", self._fallback_storage(request))

        with self._patch.multiple(
            "kb.views.suggestions",
            write_article_files=lambda article: None,
            sync_article_image_assets=lambda article, old_assets=None: None,
            clear_committed_pending_uploads=lambda request, assets: None,
        ):
            return edit_suggestion(request, article.pk)

    def test_manager_can_publish_pending_public_article(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Manager review pending article",
            body="A valid article body for manager approval.",
            filename="manager-review-pending.md",
            status=SuggestedArticle.Status.PENDING,
        )

        response = self._post_review(article, status=SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.approved_by_id, self.manager.pk)
        self.assertIsNotNone(article.approved_at)

    def test_manager_publish_of_failed_update_applies_and_clears_staged_state(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.manager,
            title="Manager failed update article",
            body="Previously approved article body.",
            filename="manager-failed-update.md",
            status=SuggestedArticle.Status.PUBLISHED,
            pending_update_title="Manager revised article",
            pending_update_body="The corrected update body is ready to publish.",
            pending_update_keywords="manager, revised",
            update_status=SuggestedArticle.UpdateStatus.FAILED,
            review_notes="Please revise the update before approval.",
        )

        # This deliberately uses the normal personal Edit route. A Manager may
        # resolve their own failed update without needing a special URL flag.
        response = self._post_review(article, status=SuggestedArticle.Status.PUBLISHED, editor_mode="edit")
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.title, "Manager revised article")
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.NONE)
        self.assertFalse(article.pending_update_body)
        self.assertFalse(article.review_notes)
        self.assertEqual(article.approved_by_id, self.manager.pk)

    def test_manager_publish_of_saved_update_draft_clears_staged_state(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.manager,
            title="Manager saved update article",
            body="Previously approved article body.",
            filename="manager-saved-update.md",
            status=SuggestedArticle.Status.PUBLISHED,
            pending_update_title="Manager saved revision",
            pending_update_body="The saved revision is ready to publish.",
            pending_update_keywords="manager, saved",
            update_status=SuggestedArticle.UpdateStatus.NONE,
        )

        response = self._post_review(article, status=SuggestedArticle.Status.PUBLISHED, editor_mode="edit")
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.title, "Manager saved revision")
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.NONE)
        self.assertFalse(article.pending_update_body)
        self.assertEqual(article.approved_by_id, self.manager.pk)

    def test_manager_reopens_failed_update_without_hiding_published_article(self):
        from kb.models import SuggestedArticle

        article = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Manager reopens failed update",
            body="Currently published article body.",
            filename="manager-reopen-update.md",
            status=SuggestedArticle.Status.PUBLISHED,
            pending_update_title="Updated title for review",
            pending_update_body="Updated body that should return to review.",
            pending_update_keywords="review",
            update_status=SuggestedArticle.UpdateStatus.FAILED,
            review_notes="Original rejection feedback.",
        )

        response = self._post_review(article, status=SuggestedArticle.Status.PENDING)
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.PENDING)
        self.assertEqual(article.pending_update_body, "Updated body that should return to review.")
        self.assertFalse(article.review_notes)
        self.assertTrue(article.review_notes_history)
