import ipaddress
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.http import Http404
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse
from django.utils import translation
from django.utils.translation import gettext as _
from urllib.parse import urlencode

from .mfa import (
    begin_pending_mfa_login,
    clear_mfa_verified,
    clear_pending_mfa_login,
    get_pending_mfa_user,
    get_or_create_mfa_device,
    mfa_is_verified,
    pending_mfa_target_name,
    PRE_MFA_USER_ID_SESSION_KEY,
    start_disabled_account_session,
    user_requires_mfa,
)
from .models import SiteSetting, UserProfile
from .auth_monitoring import log_auth_event
from .permissions import user_has_disabled_role




SESSION_STARTED_AT_KEY = "djopenkb_session_started_at"


def _split_cidr_values(raw_value):
    return [item.strip() for item in re.split(r"[,\s]+", raw_value or "") if item.strip()]


def _configured_admin_networks():
    """Return valid admin allowlist networks from Site settings.

    If the database is not ready, fall back to the model default. Invalid entries
    are ignored instead of breaking the whole site, but if no valid network is
    left, /admin/ is denied closed.
    """
    try:
        raw_value = SiteSetting.load().admin_allowed_cidrs
    except Exception:
        raw_value = SiteSetting._meta.get_field("admin_allowed_cidrs").default

    networks = []
    for value in _split_cidr_values(raw_value):
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return networks


def _request_client_ip(request):
    """Return the best client IP for admin CIDR checks behind Nginx.

    Nginx overwrites X-Real-IP with the actual remote client address before
    proxying to Django. X-Forwarded-For is used as a fallback for compatible
    proxy setups, and REMOTE_ADDR is the final direct-access fallback.
    """
    for value in (
        request.META.get("HTTP_X_REAL_IP", ""),
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0],
        request.META.get("REMOTE_ADDR", ""),
    ):
        value = (value or "").strip()
        if value:
            return value
    return ""


def _admin_cidr_allowed(request):
    networks = _configured_admin_networks()
    if not networks:
        return False

    try:
        client_ip = ipaddress.ip_address(_request_client_ip(request))
    except ValueError:
        return False

    return any(client_ip in network for network in networks)


def _get_session_timeout_days():
    try:
        return max(int(SiteSetting.load().session_timeout_days), 0)
    except Exception:
        return 30


def _get_session_started_at(request):
    raw_value = request.session.get(SESSION_STARTED_AT_KEY)
    if not raw_value:
        return None

    started_at = parse_datetime(raw_value)
    if started_at is None:
        return None
    if timezone.is_naive(started_at):
        started_at = timezone.make_aware(started_at, timezone.get_current_timezone())
    return started_at


def _mark_session_started(request):
    now = timezone.now()
    request.session[SESSION_STARTED_AT_KEY] = now.isoformat()
    request.session.modified = True
    return now


def _apply_session_cookie_expiry(request, timeout_days):
    if timeout_days > 0:
        request.session.set_expiry(timeout_days * 24 * 60 * 60)
    else:
        # 0 means browser-session only: the cookie expires when the browser closes.
        request.session.set_expiry(0)


def clear_session_started_at(request):
    request.session.pop(SESSION_STARTED_AT_KEY, None)
    request.session.modified = True


class SessionTimeoutMiddleware:
    """Expire authenticated and pending-MFA sessions by admin-defined age.

    The timeout is stored in Site settings so admins can configure how long a
    signed-in session remains valid. The default is 30 days. MFA is treated as
    part of login completion, so pending-MFA sessions are also expired.
    A value of 0 means browser-session only, not an indefinite persistent login.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _session_subject_exists(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            return True
        return bool(get_pending_mfa_user(request))

    def __call__(self, request):
        if self._session_subject_exists(request):
            timeout_days = _get_session_timeout_days()

            if timeout_days > 0:
                started_at = _get_session_started_at(request)
                if started_at is None:
                    started_at = _mark_session_started(request)

                expires_at = started_at + timezone.timedelta(days=timeout_days)
                if timezone.now() >= expires_at:
                    clear_pending_mfa_login(request)
                    clear_mfa_verified(request)
                    clear_session_started_at(request)
                    logout(request)
                    messages.warning(request, _("Your session has expired. Please sign in again."))
                    response = redirect("login")
                    return set_strict_no_cache_headers(response)

            _apply_session_cookie_expiry(request, timeout_days)

        return self.get_response(request)


class UserProfileLanguageMiddleware:
    """Activate the logged-in user's saved UI language."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            language_code = profile.preferred_language or settings.LANGUAGE_CODE

            allowed_codes = {code for code, _name in settings.LANGUAGES}
            if language_code not in allowed_codes:
                language_code = settings.LANGUAGE_CODE

            translation.activate(language_code)
            request.LANGUAGE_CODE = language_code

        response = self.get_response(request)

        if user and user.is_authenticated:
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, request.LANGUAGE_CODE)

        return response


def set_strict_no_cache_headers(response):
    """Prevent browser back/forward cache from showing stale auth/MFA pages."""
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["X-Accel-Expires"] = "0"

    existing_vary = response.get("Vary")
    if existing_vary:
        vary_parts = {part.strip() for part in existing_vary.split(",") if part.strip()}
        vary_parts.add("Cookie")
        response["Vary"] = ", ".join(sorted(vary_parts))
    else:
        response["Vary"] = "Cookie"
    return response


class DisabledUserLogoutMiddleware:
    """Restrict Disabled User sessions before normal views run.

    Disabled accounts keep a temporary authenticated session only so the clean
    /account-disabled/ page can be shown. Every other request is stopped before
    the requested function runs and redirected to that page. The session is
    cleared only when the user clicks the sign-out button.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _reverse_or_none(self, name):
        try:
            return reverse(name)
        except NoReverseMatch:
            return None

    def _is_static_or_safe_asset(self, path):
        if settings.STATIC_URL and path.startswith(settings.STATIC_URL):
            return True
        return path in {"/favicon.ico", "/robots.txt"}

    def _is_disabled_allowed_path(self, path):
        allowed_names = ("account_disabled", "logout", "set_site_language")
        for name in allowed_names:
            candidate = self._reverse_or_none(name)
            if candidate and path == candidate:
                return True
        return False

    def _redirect_disabled_user(self, request, user, *, source):
        username = user.get_username() if user else ""
        if not request.session.get("djopenkb_disabled_redirect_logged"):
            try:
                log_auth_event(
                    request,
                    event_type="password_failure",
                    success=False,
                    user=user,
                    username=username,
                    details={"reason": "account_disabled", "source": source},
                )
            except Exception:
                # Logging must never prevent the defensive redirect.
                pass
            request.session["djopenkb_disabled_redirect_logged"] = True
            request.session.modified = True

        response = redirect("account_disabled")
        return set_strict_no_cache_headers(response)

    def __call__(self, request):
        path = request.path_info or request.path
        if self._is_static_or_safe_asset(path):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user and user.is_authenticated and user_has_disabled_role(user):
            if self._is_disabled_allowed_path(path):
                return self.get_response(request)
            return self._redirect_disabled_user(request, user, source="disabled_user_middleware")

        # If an admin disables a user while that user is between password login
        # and MFA completion, create the same restricted authenticated session
        # so /account-disabled/ remains authenticated-only. Use the raw pending
        # session key here because normal pending-MFA lookup may clear disabled
        # users once their main-site access is removed.
        pending_user = None
        pending_user_id = request.session.get(PRE_MFA_USER_ID_SESSION_KEY)
        if pending_user_id:
            try:
                pending_user = get_user_model().objects.get(pk=pending_user_id, is_active=True)
            except get_user_model().DoesNotExist:
                clear_pending_mfa_login(request)

        if pending_user and user_has_disabled_role(pending_user):
            start_disabled_account_session(request, pending_user)
            response = redirect("account_disabled")
            return set_strict_no_cache_headers(response)

        return self.get_response(request)


class LocalMFARequiredMiddleware:
    """Server-side MFA login gate for all Knowledge Repository users.

    MFA is treated as part of login completion. After AD or local password
    authentication succeeds, users are stored in a pending-MFA session, not a
    fully authenticated Django session. Until setup/verification succeeds, every
    internal Knowledge Repository page redirects back to the required MFA page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _reverse_or_none(self, name):
        try:
            return reverse(name)
        except NoReverseMatch:
            return None

    def _path_is_public_asset(self, path):
        allowed_prefixes = (
            settings.STATIC_URL,
            getattr(settings, "MEDIA_URL", "/media/"),
            "/favicon.ico",
            "/robots.txt",
        )
        return any(path.startswith(prefix) for prefix in allowed_prefixes if prefix)


    def _redirect_to_target(self, request, target_name, next_url=None):
        target_path = reverse(target_name)
        path = request.path_info or request.path

        # Use the real destination the user was trying to reach, not always the
        # current request path. This matters for Django admin: after a direct
        # /admin/login/?next=/admin/ login, the current path is the admin login
        # page, but the correct post-MFA destination is /admin/.
        destination = next_url or request.get_full_path()

        if path == target_path:
            response = redirect(target_name)
        else:
            response = redirect(f"{target_path}?{urlencode({'next': destination})}")
        return set_strict_no_cache_headers(response)

    def _gate_pending_mfa_login(self, request, path):
        pending_user = get_pending_mfa_user(request)
        if not pending_user:
            return None

        if self._path_is_public_asset(path):
            return None

        logout_path = self._reverse_or_none("logout")
        if logout_path and path == logout_path:
            return None

        target_name = pending_mfa_target_name(request) or "mfa_setup"
        target_path = reverse(target_name)

        if path != target_path:
            messages.warning(
                request,
                _("Complete MFA before continuing. You are not fully signed in until MFA is completed."),
            )
            return self._redirect_to_target(request, target_name, next_url=request.get_full_path())

        return None

    def _gate_authenticated_local_user(self, request, path):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not user_requires_mfa(user):
            return None

        if mfa_is_verified(request):
            return None

        if self._path_is_public_asset(path):
            return None

        logout_path = self._reverse_or_none("logout")
        if logout_path and path == logout_path:
            return None

        target_name = pending_mfa_target_name(request) or "mfa_setup"
        target_path = reverse(target_name)

        if path != target_path:
            messages.warning(
                request,
                _("Complete MFA before continuing. You cannot access Knowledge Repository until MFA is completed."),
            )
            return self._redirect_to_target(request, target_name, next_url=request.get_full_path())

        return None

    def _admin_path_requires_mfa(self, path):
        return path.startswith("/admin/") or path == "/admin"

    def _session_user_after_login(self, request):
        user_id = request.session.get("_auth_user_id")
        if not user_id:
            return None

        User = get_user_model()
        try:
            return User.objects.get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            return None

    def _target_for_user(self, user):
        device = getattr(user, "kb_mfa_device", None) or get_or_create_mfa_device(user)
        return "mfa_verify" if device.confirmed else "mfa_setup"

    def _convert_admin_session_to_pending_mfa(self, request, user, next_url=None):
        """Turn a direct Django-admin login into a pending MFA login.

        Django admin has its own login view. Without this conversion, admin users
        can create a real Django session before completing Knowledge Repository MFA. This
        makes MFA part of the admin login criteria too.
        """
        backend = request.session.get("_auth_user_backend") or getattr(user, "backend", None)
        next_url = next_url or request.get_full_path() or reverse("admin:index")
        target_name = self._target_for_user(user)

        logout(request)
        begin_pending_mfa_login(request, user, next_url=next_url, backend=backend)
        messages.warning(
            request,
            _("Complete MFA before accessing the Django admin site."),
        )
        return self._redirect_to_target(request, target_name, next_url=next_url)

    def __call__(self, request):
        path = request.path_info or request.path

        # Pending MFA users are password-authenticated but not fully logged in.
        pending_response = self._gate_pending_mfa_login(request, path)
        if pending_response is not None:
            return pending_response

        # If a direct /admin/ request already has an authenticated session but
        # MFA was not completed, convert it into a pending-MFA session. This
        # prevents admin users from bypassing the normal Knowledge Repository login page.
        user = getattr(request, "user", None)
        if (
            self._admin_path_requires_mfa(path)
            and user
            and user.is_authenticated
            and user_requires_mfa(user)
            and not mfa_is_verified(request)
        ):
            return self._convert_admin_session_to_pending_mfa(request, user, next_url=request.get_full_path())

        authenticated_response = self._gate_authenticated_local_user(request, path)
        if authenticated_response is not None:
            return authenticated_response

        response = self.get_response(request)

        # Catch direct Django-admin login POST. The admin login view may create
        # a Django session before redirecting. If that happens, immediately
        # replace it with a pending-MFA session and redirect to MFA.
        if self._admin_path_requires_mfa(path) and not get_pending_mfa_user(request):
            session_user = self._session_user_after_login(request)
            if session_user and user_requires_mfa(session_user) and not mfa_is_verified(request):
                return self._convert_admin_session_to_pending_mfa(
                    request,
                    session_user,
                    next_url=request.GET.get("next") or reverse("admin:index"),
                )

        if get_pending_mfa_user(request):
            return set_strict_no_cache_headers(response)

        user = getattr(request, "user", None)
        if user and user.is_authenticated and user_requires_mfa(user):
            return set_strict_no_cache_headers(response)

        return response


class ForceLoginAndAdminGuardMiddleware:
    """Require authentication for every application page except the login entry.

    Public anonymous visitors may only load the login page and static assets
    needed to render it. The old article index must never be reachable at /;
    it is only available at /home/ after successful login. Django's default
    /admin/login/ endpoint is hidden with 404.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _reverse_or_none(self, name):
        try:
            return reverse(name)
        except NoReverseMatch:
            return None

    def _is_static_or_safe_asset(self, path):
        # Public static files are required for the login page CSS/JS/images.
        # Uploaded article files are deliberately not included here.
        if settings.STATIC_URL and path.startswith(settings.STATIC_URL):
            return True
        return path in {"/favicon.ico", "/robots.txt"}

    def _is_public_auth_path(self, path):
        # / and /login/ are the only normal anonymous entry points.
        # MFA pages are reachable only for pending-MFA sessions; the MFA
        # middleware validates that state before allowing completion.
        public_names = (
            "root_login",
            "login",
            "logout",
            "set_site_language",
            "mfa_setup",
            "mfa_verify",
        )
        for name in public_names:
            public_path = self._reverse_or_none(name)
            if public_path and path == public_path:
                return True
        return False

    def _is_admin_login_path(self, path):
        return path in {"/admin/login", "/admin/login/"} or path.startswith("/admin/login/")

    def _is_admin_path(self, path):
        return path == "/admin" or path.startswith("/admin/")

    def __call__(self, request):
        path = request.path_info or request.path

        # Do not expose the default Django admin login page. Admins must
        # authenticate from the main Knowledge Repository login page first.
        if self._is_admin_login_path(path):
            raise Http404()

        if self._is_static_or_safe_asset(path):
            return self.get_response(request)

        if self._is_public_auth_path(path):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            if self._is_admin_path(path):
                if not getattr(user, "is_superuser", False):
                    raise Http404()
                if not _admin_cidr_allowed(request):
                    raise Http404()

                # Defence-in-depth: /admin/ must not be reachable unless the
                # separate admin MFA step-up session is already valid. The
                # dedicated admin MFA verification URL is exempted so admins can
                # actually complete the challenge. This check backs up
                # AdminMFASessionMiddleware and prevents accidental bypass if
                # middleware order changes later.
                try:
                    admin_mfa_path = reverse("admin_mfa_verify")
                    if path != admin_mfa_path and not path.startswith(admin_mfa_path + "/"):
                        from .admin_security import admin_mfa_is_verified

                        if not admin_mfa_is_verified(request, user):
                            from urllib.parse import urlencode

                            return redirect(f"{admin_mfa_path}?{urlencode({'next': request.get_full_path()})}")
                except NoReverseMatch:
                    pass
            return self.get_response(request)

        # Anonymous users should not be able to enumerate application URLs.
        # They must know and visit / or /login/ directly.
        raise Http404()


class AuthSessionCacheControlMiddleware:
    """Apply no-store headers to login/logout/MFA and authenticated pages."""

    AUTH_PATH_NAMES = ("root_login", "login", "logout", "mfa_setup", "mfa_verify", "reset_mfa")

    def __init__(self, get_response):
        self.get_response = get_response

    def _auth_paths(self):
        paths = set()
        for name in self.AUTH_PATH_NAMES:
            try:
                paths.add(reverse(name))
            except NoReverseMatch:
                continue
        return paths

    def __call__(self, request):
        response = self.get_response(request)

        path = request.path_info or request.path
        user = getattr(request, "user", None)
        should_no_store = bool(user and user.is_authenticated)
        should_no_store = should_no_store or bool(get_pending_mfa_user(request))
        should_no_store = should_no_store or path in self._auth_paths()
        should_no_store = should_no_store or path.startswith("/admin/")

        if should_no_store:
            set_strict_no_cache_headers(response)

        return response

class AdminActivityLogMiddleware:
    """Append audit rows for state-changing Django Admin requests.

    Object-level add/change/delete details are mirrored from Django's built-in
    LogEntry table by kb.signals. This middleware catches custom admin POSTs
    such as MFA resets, lockout resets, bulk actions, and other admin forms.
    """

    STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, get_response):
        self.get_response = get_response

    def _is_admin_path(self, path):
        return path == "/admin" or path.startswith("/admin/")

    def __call__(self, request):
        response = self.get_response(request)

        try:
            path = request.path_info or request.path
            user = getattr(request, "user", None)
            if (
                self._is_admin_path(path)
                and request.method in self.STATE_CHANGING_METHODS
                and user
                and user.is_authenticated
                and user.is_staff
            ):
                from .admin_audit import infer_admin_request_context, log_admin_activity
                from .models import AdminActivityLog

                context = infer_admin_request_context(request, response=response)
                log_admin_activity(
                    request=request,
                    event_type=AdminActivityLog.EventType.ADMIN_ACTION,
                    target_app_label=context.get("target_app_label", ""),
                    target_model=context.get("target_model", ""),
                    target_object_id=context.get("target_object_id", ""),
                    target_repr=context.get("target_repr", ""),
                    change_message=context.get("change_message", ""),
                    status_code=getattr(response, "status_code", None),
                    details=context.get("details", {}),
                )
        except Exception:
            # Admin audit logging must never break the admin response.
            pass

        return response

