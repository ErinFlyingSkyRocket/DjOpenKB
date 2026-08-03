from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from kb.auth_monitoring import get_auth_lockout_identifier
from kb.models import UserProfile


class LDAPAliasLockoutIdentifierTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="alice",
            password=None,
        )
        profile, _created = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.LDAP_USER
        profile.auth_source = UserProfile.AuthSource.AD
        profile.save(update_fields=["account_type", "auth_source", "updated_at"])

    @staticmethod
    def _request(username, login_mode="ad"):
        return SimpleNamespace(
            POST={"login_mode": login_mode, "username": username},
            META={"REMOTE_ADDR": "192.0.2.10"},
        )

    def test_equivalent_ad_login_forms_share_one_user_lockout_bucket(self):
        identifiers = {
            get_auth_lockout_identifier(
                request=self._request(value),
                username=value,
                purpose="password",
            )
            for value in ("alice", "alice@example.invalid", "EXAMPLE\\alice")
        }

        self.assertEqual(identifiers, {f"password:user:{self.user.pk}"})

    def test_ad_alias_does_not_map_to_unrelated_local_account(self):
        local_user = get_user_model().objects.create_user(
            username="local-only",
            password="safe-test-password",
        )
        local_profile, _created = UserProfile.objects.get_or_create(user=local_user)
        local_profile.account_type = UserProfile.AccountType.USER
        local_profile.auth_source = UserProfile.AuthSource.LOCAL
        local_profile.save(update_fields=["account_type", "auth_source", "updated_at"])

        identifier = get_auth_lockout_identifier(
            request=self._request("EXAMPLE\\local-only"),
            username="EXAMPLE\\local-only",
            purpose="password",
        )

        self.assertNotEqual(identifier, f"password:user:{local_user.pk}")
        self.assertTrue(identifier.startswith("password:username_ip:"))

    def test_local_login_mode_keeps_local_account_identity(self):
        local_user = get_user_model().objects.create_user(
            username="local-user",
            password="safe-test-password",
        )

        identifier = get_auth_lockout_identifier(
            request=self._request("local-user", login_mode="local"),
            username="local-user",
            purpose="password",
        )

        self.assertEqual(identifier, f"password:user:{local_user.pk}")
