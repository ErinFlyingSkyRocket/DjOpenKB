from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from kb.admin import UniqueEmailUserChangeForm, UniqueEmailUserCreationForm


class UserEmailUniquenessTests(TestCase):
    def test_database_rejects_case_insensitive_duplicate_nonblank_email(self):
        User = get_user_model()
        User.objects.create_user(
            username="first-user",
            email="Unique.Address@Example.com",
            password="TestPassword123!",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="second-user",
                    email="unique.address@example.COM",
                    password="TestPassword123!",
                )

    def test_database_allows_multiple_blank_emails(self):
        User = get_user_model()
        User.objects.create_user(username="blank-one", email="", password="TestPassword123!")
        User.objects.create_user(username="blank-two", email="", password="TestPassword123!")
        self.assertEqual(User.objects.filter(email="").count(), 2)

    def test_admin_change_form_rejects_duplicate_email(self):
        User = get_user_model()
        first = User.objects.create_user(
            username="first-admin-form",
            email="first@example.com",
            password="TestPassword123!",
        )
        second = User.objects.create_user(
            username="second-admin-form",
            email="second@example.com",
            password="TestPassword123!",
        )
        form = UniqueEmailUserChangeForm(
            data={
                "username": second.username,
                "email": "FIRST@example.COM",
                "is_active": True,
                "password": second.password,
            },
            instance=second,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_admin_creation_form_rejects_duplicate_email(self):
        User = get_user_model()
        User.objects.create_user(
            username="existing-admin-form",
            email="existing@example.com",
            password="TestPassword123!",
        )
        form = UniqueEmailUserCreationForm(
            data={
                "username": "new-admin-form",
                "email": "EXISTING@example.com",
                "password1": "AnotherPassword123!",
                "password2": "AnotherPassword123!",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
