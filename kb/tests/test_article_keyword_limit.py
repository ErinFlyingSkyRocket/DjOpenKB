from django.core.exceptions import ValidationError
from django.test import TestCase

from kb.models import (
    SiteSetting,
    SuggestedArticle,
    get_article_keyword_limit,
    validate_article_keywords,
)


class ArticleKeywordLimitTests(TestCase):
    def setUp(self):
        self.setting = SiteSetting.load()
        self.setting.article_keyword_limit = 2
        self.setting.save(update_fields=["article_keyword_limit", "updated_at"])

    def test_configured_limit_is_returned(self):
        self.assertEqual(get_article_keyword_limit(), 2)

    def test_keywords_are_normalised_and_case_insensitive_duplicates_removed(self):
        self.assertEqual(
            validate_article_keywords("Network; Security\nnetwork"),
            "Network, Security",
        )

    def test_more_than_configured_keywords_are_rejected(self):
        with self.assertRaises(ValidationError) as context:
            validate_article_keywords("one, two, three")

        self.assertIn("maximum allowed is 2", context.exception.messages[0])

    def test_model_validation_checks_current_and_pending_update_keywords(self):
        article = SuggestedArticle(
            title="Keyword limit test article",
            body="Valid article body",
            keywords="one, two",
            pending_update_keywords="one, two, three",
        )

        with self.assertRaises(ValidationError) as context:
            article.full_clean()

        self.assertIn("pending_update_keywords", context.exception.message_dict)

    def test_existing_keywords_over_a_reduced_limit_are_not_silently_truncated(self):
        original = "one, two, three"
        with self.assertRaises(ValidationError):
            validate_article_keywords(original)
        self.assertEqual(original, "one, two, three")
