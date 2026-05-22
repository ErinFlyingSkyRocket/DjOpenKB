from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .models import UserProfile

try:
    from django_auth_ldap.backend import LDAPBackend
except ImportError:  # allows local non-LDAP development when LDAP_ENABLED=false
    LDAPBackend = object


def _requested_login_mode(request):
    if request is None:
        return ""
    return (request.POST.get("login_mode") or "").strip().lower()


class NextLabsLDAPBackend(LDAPBackend):
    """Active Directory / LDAP backend.

    This backend is used when the login form submits login_mode=ad. It normalizes
    common AD login formats so test users can sign in as:
    - alice
    - alice@openkb.local
    - OPENKB\alice

    The actual bind/search is still handled by django-auth-ldap.
    """

    def _normalize_username_for_ldap(self, username):
        login_value = (username or "").strip()
        if not login_value:
            return ""

        ad_domain = getattr(settings, "LDAP_AD_DOMAIN", "").strip().lower()
        netbios_domain = getattr(settings, "LDAP_NETBIOS_DOMAIN", "").strip().upper()

        # OPENKB\alice -> alice
        if "\\" in login_value:
            prefix, login_value = login_value.split("\\", 1)
            if netbios_domain and prefix.strip().upper() != netbios_domain:
                return ""

        login_value = login_value.strip()

        # alice -> alice@openkb.local when a lab AD domain is configured.
        if "@" not in login_value and ad_domain:
            return f"{login_value}@{ad_domain}"

        return login_value

    def _is_allowed_domain(self, username):
        allowed_domains = list(getattr(settings, "LDAP_ALLOWED_EMAIL_DOMAINS", []) or [])
        ad_domain = getattr(settings, "LDAP_AD_DOMAIN", "").strip().lower()
        if ad_domain and ad_domain not in allowed_domains:
            allowed_domains.append(ad_domain)

        if not allowed_domains:
            return True

        login_value = (username or "").strip().lower()
        if "@" not in login_value:
            return True

        domain = login_value.rsplit("@", 1)[1]
        return domain in allowed_domains

    def authenticate(self, request, username=None, password=None, **kwargs):
        if _requested_login_mode(request) not in {"", "ad", "ldap"}:
            return None

        if not getattr(settings, "LDAP_ENABLED", False):
            return None

        if not username or not password:
            return None

        ldap_username = self._normalize_username_for_ldap(username)
        if not ldap_username or not self._is_allowed_domain(ldap_username):
            return None

        user = super().authenticate(
            request,
            username=ldap_username,
            password=password,
            **kwargs,
        )

        if user is None:
            return None

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if profile.account_type == UserProfile.AccountType.USER:
            profile.account_type = UserProfile.AccountType.LDAP_USER
            profile.save(update_fields=["account_type", "updated_at"])

        # AD-managed users should not have a local fallback password.
        user_update_fields = []
        if user.has_usable_password():
            user.set_unusable_password()
            user_update_fields.append("password")

        # Some AD lab accounts do not have the mail attribute filled in.
        # In that case, use the validated UPN/domain login as the Django email
        # so the profile page and article metadata still show an address.
        if not (user.email or "").strip() and "@" in ldap_username:
            user.email = ldap_username.lower()
            user_update_fields.append("email")

        if user_update_fields:
            user.save(update_fields=sorted(set(user_update_fields)))

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
        if _requested_login_mode(request) in {"ad", "ldap"}:
            return None

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

    This does not connect to AD. It lets you test LDAP user flows before AD is up:
    - Existing LDAP user / LDAP admin can sign in with the placeholder password.
    - New LDAP users can be auto-created if LDAP_PLACEHOLDER_AUTO_CREATE_USERS=true.
    - A Django admin can later promote an LDAP user to LDAP admin in /admin/.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if _requested_login_mode(request) not in {"", "ad", "ldap"}:
            return None

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
