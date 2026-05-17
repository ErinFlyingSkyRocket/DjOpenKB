from django_auth_ldap.backend import LDAPBackend


class NextLabsLDAPBackend(LDAPBackend):
    """
    LDAP backend that only allows email-style Active Directory login
    using @nextlabs.com accounts.
    """

    allowed_domain = "@nextlabs.com"

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        username = username.strip().lower()

        if not username.endswith(self.allowed_domain):
            return None

        return super().authenticate(
            request,
            username=username,
            password=password,
            **kwargs,
        )