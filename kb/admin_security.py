"""Admin-site step-up MFA and short idle-session protection."""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from .auth_monitoring import (
    format_retry_after,
    get_auth_lockout_status,
    log_auth_event,
    record_auth_failure,
    record_auth_success,
)
from .mfa import (
    get_or_create_mfa_device,
    mfa_device_secret_is_readable,
    user_requires_mfa,
    verify_totp_code,
)
from .middleware import set_strict_no_cache_headers
from .permissions import user_has_disabled_role


ADMIN_MFA_VERIFIED_KEY = "knowledge_repo_admin_mfa_verified"
ADMIN_MFA_USER_ID_KEY = "knowledge_repo_admin_mfa_user_id"
ADMIN_MFA_VERIFIED_AT_KEY = "knowledge_repo_admin_mfa_verified_at"
ADMIN_MFA_LAST_ACTIVITY_AT_KEY = "knowledge_repo_admin_mfa_last_activity_at"
ADMIN_MFA_FORCE_PARAM = "fresh"

# These routes are superuser-only maintenance operations outside Django's
# /admin/ URL space. Keep this list explicit: article-manager review pages
# intentionally remain outside the admin step-up gate.
ADMIN_STEP_UP_ROUTE_NAMES = (
    "clean_stray_upload_files",
    "clean_stray_images",
    "admin_bulk_articles",
    "export_articles_zip",
    "import_articles_zip",
    "manage_orphan_articles",
    "manage_article_deletion_queue",
)


def is_admin_step_up_path(path: str) -> bool:
    """Return whether a path needs the short-lived administrator MFA grant."""
    if path == "/admin" or path.startswith("/admin/"):
        return True

    for route_name in ADMIN_STEP_UP_ROUTE_NAMES:
        try:
            if path == reverse(route_name):
                return True
        except NoReverseMatch:
            # URL configuration can be incomplete during early startup checks.
            continue
    return False


def _now_ts() -> int:
    return int(timezone.now().timestamp())


def get_admin_mfa_idle_timeout_seconds() -> int:
    """Return admin-site idle timeout in seconds.

    Site settings are the primary source so administrators can adjust this from
    Django Admin. The environment/default fallback keeps startup safe before
    the database or migration is available. Default: 600 seconds / 10 minutes.
    """
    value = getattr(settings, "ADMIN_MFA_IDLE_TIMEOUT_SECONDS", 600)

    try:
        from .models import SiteSetting

        value = SiteSetting.load().admin_mfa_idle_timeout_seconds
    except Exception:
        # Database may not be migrated yet, or the settings row may be
        # unavailable during startup. Keep the safe 10-minute fallback.
        pass

    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 600
    return max(60, min(value, 86400))


def clear_admin_mfa_session(request) -> None:
    for key in (
        ADMIN_MFA_VERIFIED_KEY,
        ADMIN_MFA_USER_ID_KEY,
        ADMIN_MFA_VERIFIED_AT_KEY,
        ADMIN_MFA_LAST_ACTIVITY_AT_KEY,
    ):
        request.session.pop(key, None)
    request.session.modified = True


def mark_admin_mfa_verified(request, user) -> None:
    now = _now_ts()
    request.session[ADMIN_MFA_VERIFIED_KEY] = True
    request.session[ADMIN_MFA_USER_ID_KEY] = str(user.pk)
    request.session[ADMIN_MFA_VERIFIED_AT_KEY] = now
    request.session[ADMIN_MFA_LAST_ACTIVITY_AT_KEY] = now
    request.session.modified = True


def admin_mfa_is_verified(request, user=None) -> bool:
    user = user or getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(
        request.session.get(ADMIN_MFA_VERIFIED_KEY)
        and str(request.session.get(ADMIN_MFA_USER_ID_KEY)) == str(user.pk)
    )


def _safe_next_url(request):
    fallback = reverse("admin:index")
    candidates = [
        (request.POST.get("next") or "").strip(),
        (request.GET.get("next") or "").strip(),
    ]
    blocked = {
        reverse("admin_mfa_verify"),
        reverse("login"),
        reverse("logout"),
    }
    for next_url in candidates:
        if not next_url:
            continue
        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            continue
        if next_url in blocked or any(next_url.startswith(f"{path}?") for path in blocked):
            continue
        return next_url
    return fallback


def _redirect_with_next(target_name: str, next_url: str):
    return redirect(f"{reverse(target_name)}?{urlencode({'next': next_url})}")


def _is_admin_user(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "is_superuser", False)
        and not user_has_disabled_role(user)
    )



def _discard_pending_messages(request) -> None:
    """Clear unrelated site messages before showing or leaving Admin MFA.

    Normal Knowledge Repository actions may add success/error messages before an
    admin user is sent to the admin step-up MFA page.  Those messages should not
    be displayed on the admin-MFA prompt or carried into Django Admin.  Admin MFA
    validation messages are passed through the template context instead.
    """
    try:
        storage = messages.get_messages(request)
        for _message in storage:
            pass
    except Exception:
        # Message storage should never block admin-MFA rendering.
        pass


def _render_admin_mfa_verify(request, next_url: str, admin_mfa_messages=None):
    _discard_pending_messages(request)
    response = render(
        request,
        "admin_mfa_verify.html",
        {
            "next": next_url,
            "admin_mfa_messages": admin_mfa_messages or [],
        },
    )
    return set_strict_no_cache_headers(response)


def admin_mfa_verify(request):
    """Require a fresh TOTP check before entering Django Admin."""
    user = getattr(request, "user", None)
    if not _is_admin_user(user):
        # Do not expose the admin MFA form to non-admin users.
        # ForceLoginAndAdminGuard normally handles this first; keep this
        # fallback for direct calls or unusual middleware ordering.
        raise Http404()

    next_url = _safe_next_url(request)

    # A fresh challenge is used when entering Django Admin from the normal
    # Knowledge Repository navbar. This makes the Admin link always ask for
    # MFA again, even if an old admin-MFA flag is still present in the
    # browser session. Direct /admin/ requests can still reuse a valid
    # step-up token until the idle timeout or leaving-admin cleanup clears it.
    if request.method == "GET" and request.GET.get(ADMIN_MFA_FORCE_PARAM) == "1":
        clear_admin_mfa_session(request)

    if not user_requires_mfa(user):
        _discard_pending_messages(request)
        messages.error(request, _("Admin access requires an active MFA-protected account."))
        return redirect("home")

    device = getattr(user, "kb_mfa_device", None) or get_or_create_mfa_device(user)
    if not device.confirmed:
        _discard_pending_messages(request)
        messages.warning(request, _("Set up MFA before accessing the admin site."))
        return _redirect_with_next("mfa_setup", reverse("admin_mfa_verify") + "?" + urlencode({"next": next_url}))

    if admin_mfa_is_verified(request, user):
        _discard_pending_messages(request)
        return redirect(next_url)

    if not mfa_device_secret_is_readable(device):
        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={"reason": "unreadable_mfa_secret", "admin_step_up": True},
        )
        return _render_admin_mfa_verify(
            request,
            next_url,
            [
                _(
                    "This MFA device cannot be verified because its secret could not be read. "
                    "Ask another administrator to reset MFA for this account."
                )
            ],
        )

    admin_mfa_messages = []

    if request.method == "POST":
        locked, retry_after, identifier = get_auth_lockout_status(
            request,
            user=user,
            purpose="admin_mfa",
        )
        if locked:
            log_auth_event(
                request,
                event_type="mfa_verify_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details={
                    "reason": "temporary_lockout",
                    "lockout_identifier": identifier,
                    "retry_after_seconds": retry_after,
                    "admin_step_up": True,
                },
            )
            admin_mfa_messages.append(
                _("Too many incorrect admin MFA codes. Please try again in %(duration)s.")
                % {"duration": format_retry_after(retry_after)}
            )
        elif verify_totp_code(device, request.POST.get("code")):
            record_auth_success(request, user=user, purpose="admin_mfa")
            device.mark_verified()
            mark_admin_mfa_verified(request, user)
            log_auth_event(
                request,
                event_type="mfa_verify_success",
                success=True,
                user=user,
                username=user.get_username(),
                details={"admin_step_up": True},
            )
            _discard_pending_messages(request)
            return redirect(next_url)
        else:
            lockout = record_auth_failure(request, user=user, purpose="admin_mfa")
            details = {
                "reason": "invalid_totp",
                "lockout_identifier": lockout.get("identifier"),
                "failure_count": lockout.get("failure_count"),
                "failure_limit": lockout.get("failure_limit"),
                "admin_step_up": True,
            }
            if lockout.get("locked"):
                details["reason"] = "temporary_lockout_created"
                details["retry_after_seconds"] = lockout.get("retry_after_seconds")
                admin_mfa_messages.append(
                    _("Too many incorrect admin MFA codes. Please try again in %(duration)s.")
                    % {"duration": format_retry_after(lockout.get("retry_after_seconds"))}
                )

            log_auth_event(
                request,
                event_type="mfa_verify_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details=details,
            )
            admin_mfa_messages.append(_("Invalid authenticator code. Please try again."))

    return _render_admin_mfa_verify(request, next_url, admin_mfa_messages)


class AdminMFASessionMiddleware:
    """Require step-up MFA before Django Admin and expire idle admin sessions.

    This does not log users out of the normal Knowledge Repository site. It only
    clears the admin-step-up verification and sends the user back to /home/ when
    the admin area has been idle too long.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _reverse_or_none(self, name):
        try:
            return reverse(name)
        except NoReverseMatch:
            return None

    def _is_admin_path(self, path: str) -> bool:
        return is_admin_step_up_path(path)

    def _is_static_or_safe_asset(self, path: str) -> bool:
        if settings.STATIC_URL and path.startswith(settings.STATIC_URL):
            return True
        media_url = getattr(settings, "MEDIA_URL", "")
        if media_url and path.startswith(media_url):
            return True
        return path in {"/favicon.ico", "/robots.txt"}

    def _clear_admin_mfa_when_leaving_admin(self, request, path: str) -> None:
        # The admin step-up token is valid only while the user stays in Django
        # Admin or one of the explicit superuser maintenance routes above. When
        # the user returns to the normal Knowledge Repository, clear it so a
        # later sensitive action requires a fresh MFA code. Ignore static/media
        # assets because Django Admin loads those while the user stays in scope.
        if self._is_static_or_safe_asset(path):
            return
        if request.session.get(ADMIN_MFA_VERIFIED_KEY):
            clear_admin_mfa_session(request)

    def _is_exempt_admin_path(self, path: str) -> bool:
        verify_path = self._reverse_or_none("admin_mfa_verify")
        exempt_paths = {
            verify_path,
            "/admin/logout/",
            "/admin/jsi18n/",
        }
        return path in {p for p in exempt_paths if p}

    def _admin_last_activity_ts(self, request) -> int | None:
        try:
            return int(request.session.get(ADMIN_MFA_LAST_ACTIVITY_AT_KEY))
        except (TypeError, ValueError):
            return None

    def _admin_timeout_response(self, request):
        clear_admin_mfa_session(request)
        messages.warning(
            request,
            _("Your admin session expired after inactivity. Verify MFA again to re-enter the admin site."),
        )
        response = redirect("home")
        return set_strict_no_cache_headers(response)

    def __call__(self, request):
        path = request.path_info or request.path

        if not self._is_admin_path(path):
            self._clear_admin_mfa_when_leaving_admin(request, path)
            return self.get_response(request)

        if self._is_exempt_admin_path(path):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if not _is_admin_user(user):
            return self.get_response(request)

        if not admin_mfa_is_verified(request, user):
            response = _redirect_with_next("admin_mfa_verify", request.get_full_path())
            return set_strict_no_cache_headers(response)

        now = _now_ts()
        last_activity = self._admin_last_activity_ts(request)
        if last_activity is None:
            request.session[ADMIN_MFA_LAST_ACTIVITY_AT_KEY] = now
            request.session.modified = True
        elif now - last_activity >= get_admin_mfa_idle_timeout_seconds():
            return self._admin_timeout_response(request)
        else:
            request.session[ADMIN_MFA_LAST_ACTIVITY_AT_KEY] = now
            request.session.modified = True

        response = self.get_response(request)
        return set_strict_no_cache_headers(response)
