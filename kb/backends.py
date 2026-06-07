import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .models import UserProfile

logger = logging.getLogger(__name__)

try:
    from django_auth_ldap.backend import LDAPBackend
except ImportError:  # allows local non-LDAP development when LDAP_ENABLED=false
    LDAPBackend = object


def _requested_login_mode(request):
    if request is None:
        return ""
    return (request.POST.get("login_mode") or "").strip().lower()




def _candidate_local_usernames_for_ldap(login_value):
    """Return possible Django usernames for an LDAP login value.

    django-auth-ldap may receive values such as:
    - bob
    - bob@example.local
    - DOMAIN\bob

    The project stores AD lab users using their sAMAccountName, so this helper
    lets us detect whether a local Django account already owns the same
    username before we allow an LDAP login to be mapped to it.
    """
    value = (login_value or "").strip()
    if not value:
        return []

    if "\\" in value:
        _prefix, value = value.split("\\", 1)
        value = value.strip()

    candidates = []
    if value:
        candidates.append(value)

    if "@" in value:
        local_part = value.split("@", 1)[0].strip()
        if local_part:
            candidates.append(local_part)

    seen = set()
    unique = []
    for candidate in candidates:
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def canonical_django_username_for_ldap(login_value):
    """Return the single Django username used for an AD/LDAP identity.

    This prevents duplicate Django users for the same AD account. For example,
    these AD login inputs all map to the same local Django username:
    - alice
    - alice@openkb.local
    - OPENKB\alice

    The LDAP search still receives the original normalized LDAP login value.
    Only the Django account username is canonicalized.
    """
    value = (login_value or "").strip()
    if not value:
        return ""

    if "\\" in value:
        _prefix, value = value.split("\\", 1)
        value = value.strip()

    if "@" in value:
        value = value.split("@", 1)[0].strip()

    return value.lower()


def _is_local_account_collision(user):
    """Return True when an existing Django user is a local account.

    LDAP users are allowed to log back into their own LDAP-created account.
    Local users/admins are not allowed to be silently reused by LDAP logins.
    """
    try:
        profile = user.kb_profile
    except UserProfile.DoesNotExist:
        profile = None

    if profile and getattr(profile, "auth_source", None) == UserProfile.AuthSource.AD:
        return False

    if profile and profile.account_type in {
        UserProfile.AccountType.LDAP_USER,
        UserProfile.AccountType.LDAP_ADMIN,
    }:
        return False

    # A local fallback password is a strong signal this is a local Django
    # account and must not be merged with an LDAP identity of the same name.
    if user.has_usable_password():
        return True

    if profile and profile.account_type in {
        UserProfile.AccountType.USER,
        UserProfile.AccountType.ADMIN,
    }:
        return True

    return bool(user.is_staff or user.is_superuser)


def _find_local_account_collision(login_value):
    UserModel = get_user_model()
    for candidate in _candidate_local_usernames_for_ldap(login_value):
        try:
            user = UserModel.objects.get(username__iexact=candidate)
        except UserModel.DoesNotExist:
            continue
        except UserModel.MultipleObjectsReturned:
            # Should not normally happen because username is unique, but reject
            # safely if data is inconsistent.
            return candidate

        if _is_local_account_collision(user):
            return user.get_username()
    return ""


def _notify_ldap_username_conflict(request, username):
    """Show a clear warning only after LDAP credentials have succeeded."""
    message = (
        "This domain account uses a username that already exists as a local "
        "DjOpenKB account. The account was not linked for safety. Please "
        "contact an administrator."
    )
    if request is not None:
        request._ldap_username_conflict = username
        try:
            messages.warning(request, message)
        except Exception:
            # Authentication backends should never break login rendering just
            # because the messages framework is unavailable.
            pass

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

        # Important: do NOT automatically convert "alice" into
        # "alice@domain". The LDAP search filter also checks sAMAccountName, and
        # AD lab users commonly log in with their sAMAccountName. If we rewrite
        # it to a UPN first, the search becomes sAMAccountName=alice@domain,
        # which will not match normal AD accounts.
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

    def ldap_to_django_username(self, username):
        """Map every AD login format to one Django username.

        django-auth-ldap uses this hook when locating/creating the Django User.
        Without it, signing in as ``alice`` and ``alice@openkb.local`` creates
        two separate Django accounts.
        """
        return canonical_django_username_for_ldap(username) or super().ldap_to_django_username(username)

    def django_to_ldap_username(self, username):
        """Do not rewrite Django usernames back to UPNs for LDAP search.

        The configured LDAP filter already searches sAMAccountName, UPN and mail,
        so ``alice`` can still authenticate directly against AD.
        """
        return (username or "").strip()

    def authenticate(self, request, username=None, password=None, **kwargs):
        if _requested_login_mode(request) not in {"", "ad", "ldap"}:
            return None

        if not getattr(settings, "LDAP_ENABLED", False):
            return None

        if not username or not password:
            return None

        login_mode = _requested_login_mode(request)
        ldap_username = self._normalize_username_for_ldap(username)
        if not ldap_username:
            logger.warning("LDAP login rejected: empty/invalid username after normalization. mode=%s input=%r", login_mode, username)
            return None

        if not self._is_allowed_domain(ldap_username):
            logger.warning("LDAP login rejected: domain not allowed. mode=%s input=%r normalized=%r", login_mode, username, ldap_username)
            return None

        canonical_username = canonical_django_username_for_ldap(ldap_username)
        local_conflict_username = _find_local_account_collision(canonical_username or ldap_username)

        logger.info(
            "LDAP login attempt started. mode=%s input=%r normalized=%r canonical_django=%r",
            login_mode,
            username,
            ldap_username,
            canonical_username,
        )

        try:
            user = super().authenticate(
                request,
                username=ldap_username,
                password=password,
                **kwargs,
            )
        except Exception:
            logger.exception("LDAP login failed with backend exception. input=%r normalized=%r", username, ldap_username)
            return None

        if user is None:
            logger.warning("LDAP login failed: django-auth-ldap returned no user. input=%r normalized=%r", username, ldap_username)
            return None

        if local_conflict_username and user.get_username().casefold() == local_conflict_username.casefold():
            _notify_ldap_username_conflict(request, local_conflict_username)
            logger.warning(
                "LDAP login blocked after successful LDAP authentication due to local username conflict. "
                "input=%r normalized=%r django_user=%r",
                username,
                ldap_username,
                user.get_username(),
            )
            return None

        logger.info("LDAP login succeeded for username=%r django_user=%r", ldap_username, user.get_username())

        profile, _ = UserProfile.objects.get_or_create(user=user)
        update_profile_fields = []
        if profile.account_type == UserProfile.AccountType.USER:
            profile.account_type = UserProfile.AccountType.LDAP_USER
            update_profile_fields.append("account_type")
        if profile.auth_source != UserProfile.AuthSource.AD:
            profile.auth_source = UserProfile.AuthSource.AD
            update_profile_fields.append("auth_source")
        if update_profile_fields:
            profile.save(update_fields=update_profile_fields + ["updated_at"])

        # AD-managed users should not have a local fallback password.
        user_update_fields = []
        if user.has_usable_password():
            user.set_unusable_password()
            user_update_fields.append("password")

        # Some AD lab accounts do not have the mail attribute filled in.
        # In that case, use the validated UPN/domain login as the Django email
        # so the profile page and article metadata still show an address.
        if not (user.email or "").strip():
            if "@" in ldap_username:
                user.email = ldap_username.lower()
                user_update_fields.append("email")
            else:
                ad_domain = getattr(settings, "LDAP_AD_DOMAIN", "").strip().lower()
                if ad_domain:
                    user.email = f"{ldap_username.lower()}@{ad_domain}"
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
                    "auth_source": UserProfile.AuthSource.AD,
                    "can_access_main_site": True,
                },
            )
        except UserModel.MultipleObjectsReturned:
            return None

        profile, _ = UserProfile.objects.get_or_create(user=user)

        if profile.auth_source != UserProfile.AuthSource.AD:
            return None

        if not profile.can_access_main_site:
            return None

        if self.user_can_authenticate(user):
            return user

        return None
