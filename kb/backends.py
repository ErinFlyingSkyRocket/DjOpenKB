from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

try:
    from django_auth_ldap.backend import LDAPBackend
except ImportError:  # allows local non-LDAP development when LDAP_ENABLED=false
    LDAPBackend = object


class NextLabsLDAPBackend(LDAPBackend):
    """LDAP backend that only allows email-style AD login using @nextlabs.com."""

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


class EmailOrUsernameModelBackend(ModelBackend):
    """Local Django login by username or email for admin-created accounts."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        UserModel = get_user_model()
        login_value = username.strip()

        try:
            if "@" in login_value:
                user = UserModel.objects.get(email__iexact=login_value)
            else:
                user = UserModel.objects.get(username__iexact=login_value)
        except UserModel.DoesNotExist:
            return None
        except UserModel.MultipleObjectsReturned:
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None
