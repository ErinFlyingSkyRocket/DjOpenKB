from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .models import UserProfile

try:
    from django_auth_ldap.backend import LDAPBackend
except ImportError:  # allows local non-LDAP development when LDAP_ENABLED=false
    LDAPBackend = object


class NextLabsLDAPBackend(LDAPBackend):
    """Real LDAP backend placeholder.

    Enable this later when AD details are ready. For now, the project can use
    PlaceholderLDAPBackend for fake LDAP sign-in/sign-up testing.
    """

    allowed_domain = "@nextlabs.com"

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        username = username.strip().lower()

        if not username.endswith(self.allowed_domain):
            return None

        user = super().authenticate(
            request,
            username=username,
            password=password,
            **kwargs,
        )

        if user is None:
            return None

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if profile.account_type == UserProfile.AccountType.USER:
            profile.account_type = UserProfile.AccountType.LDAP_USER
            profile.save(update_fields=["account_type", "updated_at"])

        if not profile.can_access_main_site:
            return None

        return user


class EmailOrUsernameModelBackend(ModelBackend):
    """Local Django login by username or email.

    Supports these local account types:
    - Admin
    - User

    LDAP user/admin accounts should authenticate through the LDAP backend.
    """

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

        profile, _ = UserProfile.objects.get_or_create(user=user)

        if not profile.can_access_main_site:
            return None

        if profile.account_type not in {
            UserProfile.AccountType.ADMIN,
            UserProfile.AccountType.USER,
        }:
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None


class PlaceholderLDAPBackend(ModelBackend):
    """Temporary fake LDAP backend for development.

    This does not connect to AD. It lets you test LDAP user flows now:
    - Existing LDAP user / LDAP admin can sign in with the placeholder password.
    - New LDAP users can be auto-created if LDAP_PLACEHOLDER_AUTO_CREATE_USERS=true.
    - A Django admin can later promote an LDAP user to LDAP admin in /admin/.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not getattr(settings, "LDAP_PLACEHOLDER_ENABLED", False):
            return None

        if not username or not password:
            return None

        placeholder_password = getattr(settings, "LDAP_PLACEHOLDER_PASSWORD", "")
        if not placeholder_password or password != placeholder_password:
            return None

        UserModel = get_user_model()
        login_value = username.strip().lower()

        try:
            if "@" in login_value:
                user = UserModel.objects.get(email__iexact=login_value)
            else:
                user = UserModel.objects.get(username__iexact=login_value)
        except UserModel.DoesNotExist:
            if not getattr(settings, "LDAP_PLACEHOLDER_AUTO_CREATE_USERS", True):
                return None

            if "@" in login_value:
                email = login_value
                username_value = login_value.split("@", 1)[0]
            else:
                email = ""
                username_value = login_value

            user = UserModel.objects.create_user(
                username=username_value,
                email=email,
                password=None,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "account_type": UserProfile.AccountType.LDAP_USER,
                    "can_access_main_site": True,
                },
            )
        except UserModel.MultipleObjectsReturned:
            return None

        profile, _ = UserProfile.objects.get_or_create(user=user)

        if profile.account_type not in {
            UserProfile.AccountType.LDAP_USER,
            UserProfile.AccountType.LDAP_ADMIN,
        }:
            return None

        if not profile.can_access_main_site:
            return None

        if self.user_can_authenticate(user):
            return user

        return None
