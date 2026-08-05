from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from kb.models import SuggestedArticle, normalize_article_title


class ArticleTitleDatabaseUniquenessTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="title-owner",
            email="title-owner@example.com",
            password="Different-Strong-Password-456!",
        )

    def _article(self, title, filename):
        return SuggestedArticle(
            owner=self.owner,
            title=title,
            body="Valid article body content.",
            status=SuggestedArticle.Status.DRAFT,
            filename=filename,
        )

    def test_save_populates_normalized_title(self):
        article = self._article("  My   Article  ", "my-article.md")
        article.save()
        self.assertEqual(article.normalized_title, normalize_article_title("My Article"))

    def test_model_validation_rejects_case_and_spacing_duplicate(self):
        self._article("My Article", "first.md").save()
        duplicate = self._article("  MY   ARTICLE ", "second.md")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_database_constraint_closes_concurrent_submission_race(self):
        self._article("My Article", "first.md").save()
        duplicate = self._article("my   article", "second.md")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                duplicate.save()
