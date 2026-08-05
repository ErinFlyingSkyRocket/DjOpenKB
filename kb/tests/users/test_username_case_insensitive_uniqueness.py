from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from kb.admin import UniqueEmailUserChangeForm, UniqueEmailUserCreationForm


class UsernameCaseInsensitiveUniquenessTests(TestCase):
    def test_database_rejects_case_insensitive_duplicate_username(self):
        User = get_user_model()
        User.objects.create_user(
            username="Alice",
            email="alice.one@example.com",
            password="TestPassword123!",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="alice",
                    email="alice.two@example.com",
                    password="TestPassword123!",
                )

    def test_admin_creation_form_rejects_case_insensitive_duplicate_username(self):
        User = get_user_model()
        User.objects.create_user(
            username="ExistingUser",
            email="existing.user@example.com",
            password="TestPassword123!",
        )
        form = UniqueEmailUserCreationForm(
            data={
                "username": "existinguser",
                "email": "new.user@example.com",
                "password1": "AnotherPassword123!",
                "password2": "AnotherPassword123!",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_admin_change_form_allows_current_username_with_different_case(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="CaseOwner",
            email="case.owner@example.com",
            password="TestPassword123!",
        )
        form = UniqueEmailUserChangeForm(
            data={
                "username": "caseowner",
                "email": user.email,
                "is_active": True,
                "password": user.password,
            },
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
