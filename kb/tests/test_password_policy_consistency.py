from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import TestCase

from kb.admin import UniqueEmailUserCreationForm


class PasswordPolicyConsistencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model()(username="alice", email="alice@example.com")

    def assertRejected(self, password):
        with self.assertRaises(ValidationError):
            validate_password(password, user=self.user)

    def test_shared_validator_enforces_all_project_rules(self):
        for password in (
            "Short1!",
            "alllowercase123!",
            "ALLUPPERCASE123!",
            "NoNumberPassword!",
            "NoSpecialPassword123",
            "Alice-Strong-Password-123!",
        ):
            with self.subTest(password=password):
                self.assertRejected(password)

    def test_shared_validator_accepts_compliant_password(self):
        validate_password("Different-Strong-Password-456!", user=self.user)

    def test_admin_creation_form_uses_shared_policy(self):
        form = UniqueEmailUserCreationForm(
            data={
                "username": "new-user",
                "email": "new-user@example.com",
                "password1": "weakpassword",
                "password2": "weakpassword",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)
