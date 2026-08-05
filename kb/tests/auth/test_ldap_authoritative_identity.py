from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from kb.backends import LDAPEmailConflict, LDAPIdentityConflict, NextLabsLDAPBackend
from kb.models import UserProfile


class LDAPAuthoritativeIdentityTests(TestCase):
    def setUp(self):
        self.backend = NextLabsLDAPBackend()

    @staticmethod
    def _ldap_user(username="jsmith", email="john.smith@example.com"):
        return SimpleNamespace(
            attrs={
                "sAMAccountName": [username],
                "mail": [email] if email else [],
            }
        )

    def _mark_as_ad_user(self, user):
        profile, _created = UserProfile.objects.get_or_create(user=user)
        profile.account_type = UserProfile.AccountType.LDAP_USER
        profile.auth_source = UserProfile.AuthSource.AD
        profile.save(update_fields=["account_type", "auth_source", "updated_at"])

    def test_new_user_uses_samaccountname_not_submitted_mail_alias(self):
        user, created = self.backend.get_or_build_user(
            "john.smith",
            self._ldap_user(),
        )

        self.assertTrue(created)
        self.assertEqual(user.username, "jsmith")

    def test_existing_ad_alias_user_is_reused_and_renamed(self):
        User = get_user_model()
        existing = User.objects.create_user(
            username="john.smith",
            email="john.smith@example.com",
            password=None,
        )
        self._mark_as_ad_user(existing)

        user, created = self.backend.get_or_build_user(
            "john.smith",
            self._ldap_user(),
        )

        self.assertFalse(created)
        self.assertEqual(user.pk, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.username, "jsmith")

    def test_authoritative_username_cannot_reuse_local_account(self):
        User = get_user_model()
        local_user = User.objects.create_user(
            username="jsmith",
            email="local@example.com",
            password="TestPassword123!",
        )
        profile, _created = UserProfile.objects.get_or_create(user=local_user)
        profile.account_type = UserProfile.AccountType.USER
        profile.auth_source = UserProfile.AuthSource.LOCAL
        profile.save(update_fields=["account_type", "auth_source", "updated_at"])

        with self.assertRaises(LDAPIdentityConflict):
            self.backend.get_or_build_user("john.smith", self._ldap_user())

    def test_directory_email_cannot_reuse_local_accounts_email(self):
        User = get_user_model()
        local_user = User.objects.create_user(
            username="local-user",
            email="john.smith@example.com",
            password="TestPassword123!",
        )
        profile, _created = UserProfile.objects.get_or_create(user=local_user)
        profile.account_type = UserProfile.AccountType.USER
        profile.auth_source = UserProfile.AuthSource.LOCAL
        profile.save(update_fields=["account_type", "auth_source", "updated_at"])

        with self.assertRaises(LDAPEmailConflict):
            self.backend.get_or_build_user("john.smith", self._ldap_user())
