from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from kb.models import (
    ARTICLE_BODY_DEFAULT_CHARACTER_LIMIT,
    SiteSetting,
    SuggestedArticle,
    count_article_body_characters,
    get_article_body_character_limit,
    validate_article_body,
)
from kb.mfa import MFA_SESSION_KEY, MFA_USER_SESSION_KEY
from kb.permissions import ROLE_ARTICLE_WRITER, assign_single_role_group


User = get_user_model()


class ArticleBodyCharacterLimitTests(TestCase):
    def setUp(self):
        self.setting = SiteSetting.load()
        self.setting.article_body_character_limit = 1000
        self.setting.save(update_fields=["article_body_character_limit", "updated_at"])

    def test_configured_limit_is_returned(self):
        self.assertEqual(get_article_body_character_limit(), 1000)

    def test_default_limit_is_one_hundred_thousand(self):
        self.assertEqual(ARTICLE_BODY_DEFAULT_CHARACTER_LIMIT, 100000)

    def test_body_at_configured_limit_is_accepted(self):
        body = "a" * 1000
        self.assertEqual(validate_article_body(body), body)

    def test_windows_line_endings_count_as_one_character(self):
        self.assertEqual(count_article_body_characters("a\r\nb"), 3)

    def test_unicode_characters_are_counted_as_characters(self):
        self.assertEqual(count_article_body_characters("A😀B"), 3)

    def test_body_over_configured_limit_is_rejected(self):
        with self.assertRaises(ValidationError) as context:
            validate_article_body("a" * 1001)
        self.assertIn("maximum allowed is 1000", context.exception.messages[0])

    def test_model_validation_checks_current_body(self):
        article = SuggestedArticle(
            title="Article body limit test",
            body="a" * 1001,
        )
        with self.assertRaises(ValidationError) as context:
            article.full_clean()
        self.assertIn("body", context.exception.message_dict)

    def test_model_validation_checks_pending_update_body(self):
        article = SuggestedArticle(
            title="Pending article body limit test",
            body="Valid article body",
            pending_update_body="a" * 1001,
        )
        with self.assertRaises(ValidationError) as context:
            article.full_clean()
        self.assertIn("pending_update_body", context.exception.message_dict)

    def test_validation_does_not_truncate_existing_content(self):
        original = "a" * 1001
        with self.assertRaises(ValidationError):
            validate_article_body(original)
        self.assertEqual(len(original), 1001)


class ArticleBodyCharacterLimitViewTests(TestCase):
    def setUp(self):
        setting = SiteSetting.load()
        setting.article_body_character_limit = 1000
        setting.save(update_fields=["article_body_character_limit", "updated_at"])

        self.user = User.objects.create_user(
            username="body-limit-writer",
            password="StrongPassword123!",
        )
        assign_single_role_group(self.user, ROLE_ARTICLE_WRITER)
        self.client.force_login(self.user)
        session = self.client.session
        session[MFA_SESSION_KEY] = True
        session[MFA_USER_SESSION_KEY] = str(self.user.pk)
        session.save()

    def test_add_article_rejects_over_limit_body_without_saving(self):
        response = self.client.post(
            reverse("suggest"),
            {
                "frm_kb_title": "Body limit view test",
                "frm_kb_body": "a" * 1001,
                "frm_kb_keywords": "test",
                "submit_action": "draft",
                "article_visibility": SuggestedArticle.Visibility.PUBLIC,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "maximum allowed is 1000")
        self.assertFalse(
            SuggestedArticle.objects.filter(title="Body limit view test").exists()
        )

    def test_add_form_exposes_configured_limit_to_editor(self):
        response = self.client.get(reverse("suggest"))
        self.assertContains(response, 'data-article-character-limit="1000"')
        self.assertContains(response, 'maxlength="1000"')
