import hashlib
import ipaddress
import logging
import re
import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse
from django.utils import translation
from django.utils.translation import gettext as _
from urllib.parse import urlencode

from .input_limits import (
    FIELD_NAME_MAX_LENGTH,
    get_field_character_limit,
    get_field_label,
)
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
from .http_cookies import set_language_cookie
from .models import SiteSetting, UserProfile
from .auth_monitoring import log_auth_event
from .permissions import user_has_disabled_role




logger = logging.getLogger(__name__)


SESSION_STARTED_AT_KEY = "djopenkb_session_started_at"


def _split_cidr_values(raw_value):
    return [item.strip() for item in re.split(r"[,\s]+", raw_value or "") if item.strip()]


def _configured_admin_networks():
    """Return ``(enabled, networks)`` for the dynamic Django Admin allowlist.

    The allowlist is disabled by default, which means source IP does not restrict
    Admin access. When enabled, malformed or empty stored configuration fails
    closed. The Django Admin form validates and normalises normal administrator
    edits before they are saved.
    """
    try:
        site_setting = SiteSetting.load()
        enabled = bool(site_setting.admin_ip_allowlist_enabled)
        raw_value = site_setting.admin_allowed_cidrs
    except Exception:
        # The current allowlist state cannot be trusted when Site settings are
        # unavailable. Deny Admin access rather than silently falling back to
        # the model default (disabled/open). Normal repository routes remain
        # available, and an administrator can repair the database/configuration
        # before retrying Admin access.
        logger.exception(
            "Unable to load the Admin IP allowlist configuration; denying Admin access."
        )
        return True, []

    if not enabled:
        return False, []

    values = _split_cidr_values(raw_value)
    if not values:
        return True, []

    networks = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # Fail closed if the database was edited outside the validated
            # Django Admin form and now contains malformed network data.
            return True, []

    return True, networks


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
    allowlist_enabled, networks = _configured_admin_networks()

    # Default/open mode: IP address is not an Admin access restriction.
    if not allowlist_enabled:
        return True

    # Enabled with no valid networks is deliberately fail-closed.
    if not networks:
        return False

    try:
        client_ip = ipaddress.ip_address(_request_client_ip(request))
    except ValueError:
        return False

    return any(client_ip in network for network in networks)


def _get_session_timeout_hours():
    try:
        return min(max(int(SiteSetting.load().session_timeout_hours), 1), 168)
    except Exception:
        return 8


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


def _apply_session_cookie_expiry(request, remaining_seconds):
    """Set the cookie expiry to the remaining fixed session lifetime.

    This intentionally receives the remaining lifetime, not the configured full
    lifetime. Calling ``set_expiry(8 hours)`` on every request would refresh the
    browser cookie even though the server-side fixed-sign-in deadline still
    blocks the user at eight hours.
    """
    request.session.set_expiry(max(1, int(remaining_seconds)))


def clear_session_started_at(request):
    request.session.pop(SESSION_STARTED_AT_KEY, None)
    request.session.modified = True




def _build_content_security_policy(nonce):
    """Return the strict per-response CSP used by HTML views.

    A fresh nonce authorises only the project-owned inline template blocks that
    cannot be static because they contain server-generated URLs, translations,
    or initial editor data. Inline event attributes and style attributes are
    deliberately forbidden instead of relying on ``unsafe-inline``.
    """
    return "; ".join(
        (
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-src https://www.youtube-nocookie.com https://player.vimeo.com",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "media-src 'self' https:",
            "connect-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "script-src-attr 'none'",
            f"style-src 'self' 'nonce-{nonce}'",
            "style-src-attr 'none'",
        )
    )


class ContentSecurityPolicyMiddleware:
    """Attach a fresh CSP nonce before templates render and send strict CSP.

    The nonce is stored only on the active request and exposed to templates by
    ``kb.context_processors.csp_nonce``. Static assets are served by Nginx and
    do not need this HTML response header.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(24)
        response = self.get_response(request)
        response["Content-Security-Policy"] = _build_content_security_policy(
            request.csp_nonce
        )
        return response




class InputLengthLimitMiddleware:
    """Reject oversized query/form values before expensive view processing.

    Browser ``maxlength`` attributes improve normal user experience, but they
    are not a security boundary. This middleware checks every GET value and
    every non-file form value against the same central limits. Multipart bodies
    are intentionally left to their dedicated upload views so a large file is
    not parsed early merely to inspect its small companion fields.
    """

    JSON_ENDPOINTS = {
        "/ask-openkb-ai/",
        "/article-video-link-validate/",
        "/article-image-upload/",
        "/article-image-delete/",
        "/article-creation-workspace/autosave/",
        "/article-creation-workspace/discard/",
        "/search/suggestions/",
        "/internal/search/suggestions/",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _is_multipart_request(request):
        return (request.content_type or "").lower().startswith("multipart/form-data")

    @staticmethod
    def _iter_querydict_values(querydict):
        for field_name, values in querydict.lists():
            yield field_name, values

    def _find_violation(self, request):
        sources = [("GET", request.GET)]
        if request.method in {"POST", "PUT", "PATCH"} and not self._is_multipart_request(request):
            sources.append((request.method, request.POST))

        for source_name, querydict in sources:
            for field_name, values in self._iter_querydict_values(querydict):
                if len(str(field_name)) > FIELD_NAME_MAX_LENGTH:
                    return {
                        "source": source_name,
                        "field_name": "",
                        "field_label": str(_("field name")),
                        "limit": FIELD_NAME_MAX_LENGTH,
                        "actual": len(str(field_name)),
                    }

                limit = get_field_character_limit(field_name)
                for value in values:
                    value_length = len(str(value or ""))
                    if value_length > limit:
                        return {
                            "source": source_name,
                            "field_name": field_name,
                            "field_label": get_field_label(field_name),
                            "limit": limit,
                            "actual": value_length,
                        }
        return None

    def _expects_json(self, request):
        path = request.path_info or request.path
        if path in self.JSON_ENDPOINTS:
            return True
        accept = (request.headers.get("Accept") or "").lower()
        return "application/json" in accept

    def _oversized_response(self, request, violation):
        message = _(
            "The submitted %(field)s exceeds the maximum of %(limit)d characters."
        ) % {
            "field": violation["field_label"],
            "limit": violation["limit"],
        }

        logger.warning(
            "Rejected oversized request input: method=%s path=%s source=%s field=%s length=%s limit=%s",
            request.method,
            request.path,
            violation["source"],
            violation["field_name"] or "<field-name>",
            violation["actual"],
            violation["limit"],
        )

        if self._expects_json(request):
            return JsonResponse(
                {
                    "error": message,
                    "field": violation["field_name"],
                    "max_length": violation["limit"],
                },
                status=400,
            )

        from .views.errors import render_http_error

        return render_http_error(
            request,
            400,
            error_message=message,
            error_help=_(
                "Shorten the submitted value and try again. The browser normally prevents values beyond this limit."
            ),
        )

    def __call__(self, request):
        try:
            violation = self._find_violation(request)
        except RequestDataTooBig:
            from .views.errors import render_http_error

            return render_http_error(request, 413)
        except (TooManyFieldsSent, TooManyFilesSent):
            from .views.errors import render_http_error

            return render_http_error(
                request,
                400,
                error_message=_("Too many form fields were submitted."),
                error_help=_("Reload the page and submit the form again using the available fields."),
            )

        if violation:
            return self._oversized_response(request, violation)
        return self.get_response(request)


class NginxErrorPageMiddleware:
    """Render friendly pages for errors generated at the Nginx edge.

    Normal browser requests cannot activate this middleware because
    ``nginx/proxy_params`` removes the private marker header. Nginx adds it
    back only inside its ``internal`` error locations.
    """

    ALLOWED_STATUS_CODES = {400, 403, 404, 405, 413, 429, 500}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw_status = request.META.get("HTTP_X_DJOPENKB_NGINX_ERROR", "").strip()
        if not raw_status:
            return self.get_response(request)

        try:
            status_code = int(raw_status)
        except (TypeError, ValueError):
            return self.get_response(request)

        if status_code not in self.ALLOWED_STATUS_CODES:
            return self.get_response(request)

        from .views.errors import render_http_error

        return render_http_error(request, status_code)


REQUEST_RATE_LIMIT_CONFIG_CACHE_KEY = "request_rate_limit:configured-limits"
REQUEST_RATE_LIMIT_CONFIG_CACHE_SECONDS = 30
REQUEST_RATE_LIMIT_DEFAULTS = {
    "login": 8,
    "mfa": 10,
    "admin": 120,
}


def _bounded_rate_limit(value, default, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return min(max(value, 0), maximum)


def _configured_request_rate_limits():
    """Return Redis-backed application request limits from Site settings."""
    try:
        cached = cache.get(REQUEST_RATE_LIMIT_CONFIG_CACHE_KEY)
        if isinstance(cached, dict):
            return cached
    except Exception:
        cached = None

    try:
        site_setting = SiteSetting.load()
        limits = {
            "login": _bounded_rate_limit(
                site_setting.login_request_limit_per_minute, 8, 120
            ),
            "mfa": _bounded_rate_limit(
                site_setting.mfa_request_limit_per_minute, 10, 120
            ),
            "admin": _bounded_rate_limit(
                site_setting.admin_request_limit_per_minute, 120, 600
            ),
        }
    except Exception:
        logger.exception(
            "Unable to load configurable request-rate limits; using secure defaults."
        )
        limits = dict(REQUEST_RATE_LIMIT_DEFAULTS)

    try:
        cache.set(
            REQUEST_RATE_LIMIT_CONFIG_CACHE_KEY,
            limits,
            REQUEST_RATE_LIMIT_CONFIG_CACHE_SECONDS,
        )
    except Exception:
        pass
    return limits


def _request_rate_limit_scope(request):
    """Return the configured rate-limit scope for one unsafe request."""
    if request.method != "POST":
        return ""

    path = request.path_info or request.path
    if path in {"/", "/login/"}:
        return "login"

    if path in {
        "/mfa/setup/",
        "/mfa/verify/",
        "/mfa/reset/",
        "/mfa/reset/setup/",
        "/admin/mfa/start/",
        "/admin/mfa/verify/",
    }:
        return "mfa"

    if path in {"/admin", "/admin/"} or path.startswith("/admin/"):
        if path in {"/admin/login", "/admin/login/", "/admin/logout/"}:
            return ""
        return "admin"

    return ""


def _request_rate_limit_subject(request, scope):
    if scope == "admin":
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "pk", None):
            return f"user:{user.pk}"

    client_ip = _request_client_ip(request) or "unknown"
    return "ip:" + hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:24]


class ConfigurableRequestRateLimitMiddleware:
    """Apply Site-setting request limits with atomic shared Redis counters.

    Nginx keeps a deliberately higher fixed outer ceiling. These lower limits
    are editable in Django Admin and take effect without reloading Nginx.
    Account/password/MFA lockouts remain separate and continue to provide the
    authoritative per-user progressive blocking policy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        scope = _request_rate_limit_scope(request)
        if not scope:
            return self.get_response(request)

        limits = _configured_request_rate_limits()
        limit = int(limits.get(scope, 0) or 0)
        if limit <= 0:
            return self.get_response(request)

        now = int(time.time())
        window = now // 60
        retry_after = max(1, 60 - (now % 60))
        subject = _request_rate_limit_subject(request, scope)
        key = f"request_rate_limit:{scope}:{subject}:{window}"

        try:
            cache.add(key, 0, timeout=70)
            count = int(cache.incr(key))
            try:
                cache.touch(key, timeout=70)
            except (AttributeError, NotImplementedError):
                pass
        except Exception:
            logger.exception(
                "Shared request-rate-limit storage is unavailable; rejecting protected POST. scope=%s",
                scope,
            )
            from .views.errors import render_http_error

            response = render_http_error(
                request,
                503,
                error_message=_("The security rate-limit service is temporarily unavailable."),
                error_help=_("Please wait briefly and try again."),
            )
            response["Retry-After"] = "60"
            return response

        if count > limit:
            logger.warning(
                "Configurable request limit exceeded. scope=%s subject=%s count=%s limit=%s",
                scope,
                subject,
                count,
                limit,
            )
            from .views.errors import render_http_error

            response = render_http_error(request, 429)
            response["Retry-After"] = str(retry_after)
            return response

        return self.get_response(request)


class SessionTimeoutMiddleware:
    """Expire authenticated and pending-MFA sessions by admin-defined age.

    The timeout is stored in Site settings so admins can configure how long a
    signed-in session remains valid. The default is 8 hours. Pending-MFA
    sessions also keep this fixed upper bound, while the separate MFA login
    completion timeout normally expires them much sooner. The allowed
    administrator setting range is 1 to 168 hours.
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
            timeout_hours = _get_session_timeout_hours()
            started_at = _get_session_started_at(request)
            if started_at is None:
                started_at = _mark_session_started(request)

            expires_at = started_at + timezone.timedelta(hours=timeout_hours)
            now = timezone.now()
            if now >= expires_at:
                clear_pending_mfa_login(request)
                clear_mfa_verified(request)
                clear_session_started_at(request)
                logout(request)
                messages.warning(request, _("Your session has expired. Please sign in again."))
                response = redirect("login")
                return set_strict_no_cache_headers(response)

            # Keep the browser cookie aligned with the absolute sign-in deadline.
            # The server-side timestamp remains the authoritative guard.
            remaining_seconds = int((expires_at - now).total_seconds())
            _apply_session_cookie_expiry(request, remaining_seconds)

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
            set_language_cookie(response, request.LANGUAGE_CODE)

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

        cancel_path = self._reverse_or_none("mfa_cancel")
        if cancel_path and path == cancel_path:
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

    def _is_public_auth_path(self, request, path):
        # / and /login/ are the only normal anonymous entry points.
        # MFA pages are reachable only for pending-MFA sessions; the MFA
        # middleware validates that state before allowing completion.
        # /logout/ is intentionally not public; pending-MFA users cancel using
        # the dedicated POST-only mfa_cancel endpoint instead.
        cancel_path = self._reverse_or_none("mfa_cancel")
        if cancel_path and path == cancel_path:
            return request.method == "POST" and bool(get_pending_mfa_user(request))

        public_names = (
            "root_login",
            "login",
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

        if self._is_public_auth_path(request, path):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            # Keep sensitive superuser maintenance routes outside /admin/ under
            # the same CIDR and fresh-MFA boundary as Django Admin itself.
            # This stays intentionally narrower than /profile/admin/ because
            # article-manager review pages have their own role model.
            try:
                from .admin_security import admin_mfa_is_verified, is_admin_step_up_path

                requires_admin_step_up = is_admin_step_up_path(path)
                admin_mfa_path = reverse("admin_mfa_verify")
                admin_mfa_start_path = reverse("admin_mfa_start")
            except NoReverseMatch:
                requires_admin_step_up = self._is_admin_path(path)
                admin_mfa_path = None
                admin_mfa_start_path = None

            if requires_admin_step_up:
                if not getattr(user, "is_superuser", False):
                    raise Http404()
                if not _admin_cidr_allowed(request):
                    raise Http404()

                # Defence-in-depth: every protected route must have a valid
                # short-lived admin MFA grant. The verify URL itself is exempt
                # so the user can complete the challenge.
                admin_mfa_exempt_paths = {
                    candidate
                    for candidate in (admin_mfa_path, admin_mfa_start_path)
                    if candidate
                }
                if path not in admin_mfa_exempt_paths:
                    if not admin_mfa_is_verified(request, user):
                        from urllib.parse import urlencode

                        return redirect(
                            f"{admin_mfa_path}?"
                            f"{urlencode({'next': request.get_full_path(), 'entry': '1'})}"
                        )
            return self.get_response(request)

        # Anonymous users should not be able to enumerate application URLs.
        # They must know and visit / or /login/ directly.
        raise Http404()


class AuthSessionCacheControlMiddleware:
    """Apply no-store headers to login/logout/MFA and authenticated pages."""

    AUTH_PATH_NAMES = (
        "root_login",
        "login",
        "logout",
        "mfa_setup",
        "mfa_verify",
        "mfa_cancel",
        "reset_mfa",
        "mfa_reset_setup",
        "mfa_reset_cancel",
    )

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


class NoIndexRobotsHeaderMiddleware:
    """Ask search engines not to index the private Knowledge Repository.

    This is defence-in-depth for public deployment. It does not replace
    authentication or authorization; it only helps prevent cooperative search
    engines from indexing login pages, error pages, or any page they somehow
    receive.
    """

    HEADER_VALUE = "noindex, nofollow, noarchive, nosnippet, noimageindex"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path_info or request.path

        # robots.txt must remain a normal text file for crawler discovery.
        if path != "/robots.txt":
            response["X-Robots-Tag"] = self.HEADER_VALUE

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

