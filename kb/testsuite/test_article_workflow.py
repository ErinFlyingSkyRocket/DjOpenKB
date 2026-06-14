from django.test import TestCase
from django.urls import reverse

from kb.models import ArticleVote, SuggestedArticle
from kb.permissions import ROLE_ADMIN_USERS, ROLE_ARTICLE_MANAGER, ROLE_ARTICLE_WRITER, ROLE_REGULAR_USER

from .helpers import DjOpenKBTestMixin


class ArticleCreationWorkflowTests(DjOpenKBTestMixin, TestCase):
    def test_regular_user_cannot_create_article(self):
        regular = self.make_user("workflow_regular", role=ROLE_REGULAR_USER)
        client = self.login_client(regular)
        response = client.post(reverse("suggest"), self.common_article_post_data())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(SuggestedArticle.objects.filter(title="A Valid Test Article").count(), 0)

    def test_writer_can_save_draft_and_submit_pending(self):
        writer = self.make_user("workflow_writer", role=ROLE_ARTICLE_WRITER)
        client = self.login_client(writer)
        with self.patch_article_file_writes():
            draft_response = client.post(
                reverse("suggest"),
                self.common_article_post_data(title="Writer Draft Article", submit_action="draft"),
            )
            pending_response = client.post(
                reverse("suggest"),
                self.common_article_post_data(title="Writer Pending Article", submit_action="submit"),
            )
        self.assertEqual(draft_response.status_code, 302)
        self.assertEqual(pending_response.status_code, 302)
        self.assertEqual(SuggestedArticle.objects.get(title="Writer Draft Article").status, SuggestedArticle.Status.DRAFT)
        self.assertEqual(SuggestedArticle.objects.get(title="Writer Pending Article").status, SuggestedArticle.Status.PENDING)

    def test_admin_can_publish_directly_from_suggest_form(self):
        admin = self.make_user("workflow_admin", role=ROLE_ADMIN_USERS)
        client = self.login_client(admin)
        with self.patch_article_file_writes():
            response = client.post(
                reverse("suggest"),
                self.common_article_post_data(title="Admin Direct Publish", submit_action="submit"),
            )
        self.assertEqual(response.status_code, 302)
        article = SuggestedArticle.objects.get(title="Admin Direct Publish")
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.approved_by, admin)
        self.assertIsNotNone(article.approved_at)

    def test_duplicate_article_title_is_rejected(self):
        writer = self.make_user("workflow_dup_writer", role=ROLE_ARTICLE_WRITER)
        self.make_article("Password Reset Guide", owner=writer, status=SuggestedArticle.Status.PUBLISHED)
        client = self.login_client(writer)
        with self.patch_article_file_writes():
            response = client.post(
                reverse("suggest"),
                self.common_article_post_data(title=" password   reset guide ", submit_action="submit"),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SuggestedArticle.objects.filter(title__icontains="password").count(), 1)


class ArticleReviewWorkflowTests(DjOpenKBTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.writer = self.make_user("review_writer", role=ROLE_ARTICLE_WRITER)
        self.manager = self.make_user("review_manager", role=ROLE_ARTICLE_MANAGER)
        self.admin = self.make_user("review_admin", role=ROLE_ADMIN_USERS)

    def test_manager_can_approve_pending_article(self):
        article = self.make_article("Pending Needs Approval", owner=self.writer, status=SuggestedArticle.Status.PENDING)
        client = self.login_client(self.manager)
        with self.patch_article_file_writes():
            response = client.post(
                reverse("edit_suggestion", kwargs={"article_id": article.pk}),
                self.common_article_post_data(
                    title=article.title,
                    body=article.body,
                    keywords=article.keywords,
                    status=SuggestedArticle.Status.PUBLISHED,
                ),
            )
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.approved_by, self.manager)

    def test_manager_must_enter_review_notes_when_rejecting(self):
        article = self.make_article("Pending Missing Notes", owner=self.writer, status=SuggestedArticle.Status.PENDING)
        client = self.login_client(self.manager)
        with self.patch_article_file_writes():
            response = client.post(
                reverse("edit_suggestion", kwargs={"article_id": article.pk}),
                self.common_article_post_data(
                    title=article.title,
                    body=article.body,
                    keywords=article.keywords,
                    status=SuggestedArticle.Status.FAILED,
                ),
            )
        self.assertEqual(response.status_code, 200)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.PENDING)

    def test_manager_can_reject_pending_article_with_notes(self):
        article = self.make_article("Pending With Notes", owner=self.writer, status=SuggestedArticle.Status.PENDING)
        client = self.login_client(self.manager)
        with self.patch_article_file_writes():
            response = client.post(
                reverse("edit_suggestion", kwargs={"article_id": article.pk}),
                self.common_article_post_data(
                    title=article.title,
                    body=article.body,
                    keywords=article.keywords,
                    status=SuggestedArticle.Status.FAILED,
                    review_notes="Please add more screenshots.",
                ),
            )
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.status, SuggestedArticle.Status.FAILED)
        self.assertIn("screenshots", article.review_notes)
        self.assertTrue(article.review_notes_history)

    def test_writer_editing_published_article_creates_pending_update(self):
        article = self.make_article("Published Stable Article", owner=self.writer, status=SuggestedArticle.Status.PUBLISHED, keywords="stable")
        client = self.login_client(self.writer)
        with self.patch_article_file_writes():
            response = client.post(
                reverse("edit_suggestion", kwargs={"article_id": article.pk}),
                self.common_article_post_data(
                    title="Published Stable Article Updated",
                    body="Updated body waiting for approval.",
                    keywords="stable, update",
                    submit_action="submit",
                ),
            )
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.title, "Published Stable Article")
        self.assertEqual(article.status, SuggestedArticle.Status.PUBLISHED)
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.PENDING)
        self.assertEqual(article.pending_update_title, "Published Stable Article Updated")

    def test_manager_can_approve_pending_update(self):
        article = self.make_article(
            "Published Update Approval",
            owner=self.writer,
            status=SuggestedArticle.Status.PUBLISHED,
            pending_update_title="Approved Updated Title",
            pending_update_body="Approved updated body.",
            pending_update_keywords="approved, update",
            update_status=SuggestedArticle.UpdateStatus.PENDING,
        )
        client = self.login_client(self.manager)
        with self.patch_article_file_writes():
            response = client.post(
                reverse("edit_suggestion", kwargs={"article_id": article.pk}),
                self.common_article_post_data(
                    title="Approved Updated Title",
                    body="Approved updated body.",
                    keywords="approved, update",
                    status=SuggestedArticle.Status.PUBLISHED,
                ),
            )
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.title, "Approved Updated Title")
        self.assertEqual(article.update_status, SuggestedArticle.UpdateStatus.NONE)
        self.assertEqual(article.pending_update_title, "")

    def test_article_vote_toggle_update_and_remove(self):
        article = self.make_article("Vote Toggle Article", owner=self.writer, status=SuggestedArticle.Status.PUBLISHED)
        voter = self.make_user("vote_regular", role=ROLE_REGULAR_USER)
        client = self.login_client(voter)
        url = reverse("vote_article", kwargs={"article_id": article.pk})
        self.assertEqual(client.post(url, {"vote": "up"}).status_code, 302)
        self.assertEqual(ArticleVote.objects.get(article=article, user=voter).value, ArticleVote.VoteValue.UP)
        self.assertEqual(client.post(url, {"vote": "down"}).status_code, 302)
        self.assertEqual(ArticleVote.objects.get(article=article, user=voter).value, ArticleVote.VoteValue.DOWN)
        self.assertEqual(client.post(url, {"vote": "down"}).status_code, 302)
        self.assertFalse(ArticleVote.objects.filter(article=article, user=voter).exists())
