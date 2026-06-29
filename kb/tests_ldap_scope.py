from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class LdapScopeSettingsTests(SimpleTestCase):
    def test_no_ad_group_membership_restriction_is_configured(self):
        self.assertFalse(hasattr(settings, "AUTH_LDAP_REQUIRE_GROUP"))
        self.assertFalse(hasattr(settings, "AUTH_LDAP_GROUP_SEARCH"))
        self.assertFalse(hasattr(settings, "AUTH_LDAP_GROUP_TYPE"))

    def test_user_search_scope_remains_configured_when_ldap_is_enabled(self):
        # This protects the LDAP user-search configuration without restricting
        # authentication to a separate AD security group.
        source = Path(settings.__file__).read_text(encoding="utf-8")
        self.assertIn("LDAP_USER_SEARCH_BASE", source)
        self.assertIn("AUTH_LDAP_USER_SEARCH", source)
