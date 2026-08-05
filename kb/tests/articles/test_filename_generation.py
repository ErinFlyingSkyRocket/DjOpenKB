from django.contrib.auth import get_user_model
from django.test import TestCase

from kb.models import SuggestedArticle
from kb.views.services import ensure_article_filename


class ArticleFilenameGenerationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="filename-test-user",
            password="safe-test-password",
        )

    def test_equivalent_slugs_get_distinct_collision_safe_filenames(self):
        first = SuggestedArticle(
            owner=self.user,
            title="Alpha & Beta",
            body="First article body",
        )
        ensure_article_filename(first)
        first.save()

        second = SuggestedArticle(
            owner=self.user,
            title="Alpha -- Beta",
            body="Second article body",
        )
        ensure_article_filename(second)

        self.assertNotEqual(first.filename, second.filename)
        self.assertTrue(second.filename.endswith(".md"))
        self.assertIn("alpha-beta", second.filename)
