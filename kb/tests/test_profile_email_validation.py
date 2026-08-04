from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from kb.views.auth import _validate_profile_email


class ProfileEmailValidationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="local-user",
            email="local.user@example.com",
            password="TestPassword123!",
        )

    def test_valid_email_is_trimmed_and_preserved(self):
        email = _validate_profile_email(self.user, "  New.Address@Example.com  ")
        self.assertEqual(email, "New.Address@Example.com")

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(ValidationError):
            _validate_profile_email(self.user, "not-an-email")

    def test_empty_email_is_rejected(self):
        with self.assertRaises(ValidationError):
            _validate_profile_email(self.user, "   ")

    def test_email_longer_than_model_field_is_rejected(self):
        max_length = self.user._meta.get_field("email").max_length
        oversized = f"{'a' * max_length}@example.com"
        with self.assertRaises(ValidationError):
            _validate_profile_email(self.user, oversized)

    def test_case_insensitive_duplicate_email_is_rejected(self):
        User = get_user_model()
        User.objects.create_user(
            username="other-user",
            email="Already.Used@Example.com",
            password="OtherPassword123!",
        )

        with self.assertRaises(ValidationError) as context:
            _validate_profile_email(self.user, "already.used@example.COM")

        self.assertEqual(context.exception.code, "duplicate")

    def test_current_users_own_email_is_allowed(self):
        email = _validate_profile_email(self.user, "LOCAL.USER@EXAMPLE.COM")
        self.assertEqual(email, "LOCAL.USER@EXAMPLE.COM")
