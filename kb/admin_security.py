"""Admin-site step-up MFA and short idle-session protection."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .auth_monitoring import (
    build_auth_lockout_ui_context,
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
ADMIN_MFA_CHALLENGE_STARTED_AT_KEY = "knowledge_repo_admin_mfa_challenge_started_at"
ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY = "knowledge_repo_admin_mfa_challenge_expires_at"
ADMIN_MFA_CHALLENGE_EXPIRED_KEY = "knowledge_repo_admin_mfa_challenge_expired"
ADMIN_MFA_CHALLENGE_ID_KEY = "knowledge_repo_admin_mfa_challenge_id"
ADMIN_MFA_LOCKOUT_DEFERRED_KEY = "knowledge_repo_admin_mfa_lockout_deferred"
ADMIN_MFA_ENTRY_PARAM = "entry"

ADMIN_MFA_VERIFICATION_TIMEOUT_DEFAULT_SECONDS = 60
ADMIN_MFA_VERIFICATION_TIMEOUT_MIN_SECONDS = 30
ADMIN_MFA_VERIFICATION_TIMEOUT_MAX_SECONDS = 900

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


def get_admin_mfa_verification_timeout_seconds() -> int:
    """Return the separate deadline for completing Admin step-up MFA.

    This setting is independent from the normal login MFA completion timeout.
    The database-backed Site setting is primary; the fallback keeps startup and
    migration commands safe before the settings table is available.
    """
    value = getattr(
        settings,
        "ADMIN_MFA_VERIFICATION_TIMEOUT_SECONDS",
        ADMIN_MFA_VERIFICATION_TIMEOUT_DEFAULT_SECONDS,
    )

    try:
        from .models import SiteSetting

        value = SiteSetting.load().admin_mfa_verification_timeout_seconds
    except Exception:
        pass

    try:
        value = int(value)
    except (TypeError, ValueError):
        value = ADMIN_MFA_VERIFICATION_TIMEOUT_DEFAULT_SECONDS

    return max(
        ADMIN_MFA_VERIFICATION_TIMEOUT_MIN_SECONDS,
        min(value, ADMIN_MFA_VERIFICATION_TIMEOUT_MAX_SECONDS),
    )


def _admin_mfa_challenge_ts(request, key: str) -> int | None:
    try:
        return int(request.session.get(key))
    except (TypeError, ValueError):
        return None


def admin_mfa_challenge_id(request) -> str | None:
    value = request.session.get(ADMIN_MFA_CHALLENGE_ID_KEY)
    if not isinstance(value, str) or not value:
        return None
    return value


def admin_mfa_deadline_is_deferred(request) -> bool:
    return bool(request.session.get(ADMIN_MFA_LOCKOUT_DEFERRED_KEY))


def start_new_admin_mfa_challenge(request, *, defer_deadline: bool = False) -> tuple[str, int | None]:
    """Replace any pending Admin MFA attempt with one new challenge.

    The challenge identifier is always rotated. Its short verification deadline
    starts immediately unless a longer Admin-MFA cooldown is already active.
    """
    clear_admin_mfa_challenge(request)
    challenge_id = secrets.token_urlsafe(32)
    request.session[ADMIN_MFA_CHALLENGE_ID_KEY] = challenge_id

    expires_at = None
    if defer_deadline:
        request.session[ADMIN_MFA_LOCKOUT_DEFERRED_KEY] = True
    else:
        started_at = _now_ts()
        expires_at = started_at + get_admin_mfa_verification_timeout_seconds()
        request.session[ADMIN_MFA_CHALLENGE_STARTED_AT_KEY] = started_at
        request.session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY] = expires_at
    request.session.modified = True
    return challenge_id, expires_at


def defer_admin_mfa_deadline_for_lockout(request) -> None:
    """Pause the short Admin verification window during a longer cooldown."""
    if admin_mfa_challenge_id(request) is None:
        return

    already_deferred = admin_mfa_deadline_is_deferred(request)
    had_deadline = bool(
        request.session.get(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY)
        or request.session.get(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY)
    )
    request.session.pop(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, None)
    request.session.pop(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, None)
    request.session.pop(ADMIN_MFA_CHALLENGE_EXPIRED_KEY, None)
    if not already_deferred or had_deadline:
        request.session[ADMIN_MFA_CHALLENGE_ID_KEY] = secrets.token_urlsafe(32)
    request.session[ADMIN_MFA_LOCKOUT_DEFERRED_KEY] = True
    request.session.modified = True


def ensure_admin_mfa_challenge_deadline(request) -> int | None:
    """Ensure an existing challenge has one fixed deadline and return it."""
    if admin_mfa_challenge_id(request) is None:
        return None
    if admin_mfa_deadline_is_deferred(request):
        return None

    started_at = _admin_mfa_challenge_ts(
        request,
        ADMIN_MFA_CHALLENGE_STARTED_AT_KEY,
    )
    expires_at = _admin_mfa_challenge_ts(
        request,
        ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY,
    )

    if started_at is None or expires_at is None or expires_at <= started_at:
        started_at = _now_ts()
        expires_at = started_at + get_admin_mfa_verification_timeout_seconds()
        request.session[ADMIN_MFA_CHALLENGE_STARTED_AT_KEY] = started_at
        request.session[ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY] = expires_at
        request.session.modified = True
    return expires_at


def resume_admin_mfa_deadline_after_lockout(request) -> int | None:
    """Start a fresh fixed Admin MFA window after the cooldown ends."""
    if admin_mfa_challenge_id(request) is None:
        return None
    if admin_mfa_deadline_is_deferred(request):
        request.session.pop(ADMIN_MFA_LOCKOUT_DEFERRED_KEY, None)
        request.session.modified = True
    return ensure_admin_mfa_challenge_deadline(request)


def admin_mfa_seconds_remaining(request) -> int | None:
    expires_at = _admin_mfa_challenge_ts(
        request,
        ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY,
    )
    if expires_at is None:
        return None
    return max(0, expires_at - _now_ts())


def admin_mfa_challenge_has_expired(request) -> bool:
    remaining = admin_mfa_seconds_remaining(request)
    return remaining is not None and remaining <= 0


def admin_mfa_challenge_is_expired_state(request) -> bool:
    return bool(request.session.get(ADMIN_MFA_CHALLENGE_EXPIRED_KEY))


def clear_admin_mfa_challenge(request) -> None:
    for key in (
        ADMIN_MFA_CHALLENGE_ID_KEY,
        ADMIN_MFA_CHALLENGE_STARTED_AT_KEY,
        ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY,
        ADMIN_MFA_CHALLENGE_EXPIRED_KEY,
        ADMIN_MFA_LOCKOUT_DEFERRED_KEY,
    ):
        request.session.pop(key, None)
    request.session.modified = True


def mark_admin_mfa_challenge_expired(request) -> None:
    request.session.pop(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY, None)
    request.session.pop(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY, None)
    request.session.pop(ADMIN_MFA_LOCKOUT_DEFERRED_KEY, None)
    request.session[ADMIN_MFA_CHALLENGE_EXPIRED_KEY] = True
    request.session.modified = True


def clear_admin_mfa_session(request) -> None:
    for key in (
        ADMIN_MFA_VERIFIED_KEY,
        ADMIN_MFA_USER_ID_KEY,
        ADMIN_MFA_VERIFIED_AT_KEY,
        ADMIN_MFA_LAST_ACTIVITY_AT_KEY,
        ADMIN_MFA_CHALLENGE_ID_KEY,
        ADMIN_MFA_CHALLENGE_STARTED_AT_KEY,
        ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY,
        ADMIN_MFA_CHALLENGE_EXPIRED_KEY,
        ADMIN_MFA_LOCKOUT_DEFERRED_KEY,
    ):
        request.session.pop(key, None)
    request.session.modified = True


def mark_admin_mfa_verified(request, user) -> None:
    clear_admin_mfa_challenge(request)
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
        reverse("admin_mfa_start"),
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


def _redirect_with_next(target_name: str, next_url: str, *, entry: bool = False):
    params = {"next": next_url}
    if entry:
        params[ADMIN_MFA_ENTRY_PARAM] = "1"
    return redirect(f"{reverse(target_name)}?{urlencode(params)}")


def _canonical_admin_mfa_verify_url(next_url: str) -> str:
    return f"{reverse('admin_mfa_verify')}?{urlencode({'next': next_url})}"


def admin_mfa_challenge_matches(request, submitted_challenge_id: str) -> bool:
    current_challenge_id = admin_mfa_challenge_id(request)
    submitted_challenge_id = (submitted_challenge_id or "").strip()
    return bool(
        current_challenge_id
        and submitted_challenge_id
        and secrets.compare_digest(current_challenge_id, submitted_challenge_id)
    )


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


def _prepare_admin_mfa_timing(request, user):
    """Make the longer Admin-MFA cooldown authoritative over its short timer."""
    locked, retry_after, identifier = get_auth_lockout_status(
        request,
        user=user,
        purpose="admin_mfa",
    )
    if admin_mfa_challenge_id(request) is not None:
        if locked:
            defer_admin_mfa_deadline_for_lockout(request)
        elif not admin_mfa_challenge_is_expired_state(request):
            resume_admin_mfa_deadline_after_lockout(request)
    return locked, retry_after, identifier


def _render_admin_mfa_verify(
    request,
    next_url: str,
    admin_mfa_messages=None,
):
    _discard_pending_messages(request)
    user = getattr(request, "user", None)
    locked, retry_after, _identifier = _prepare_admin_mfa_timing(request, user)
    remaining = admin_mfa_seconds_remaining(request)
    rate_limit_context = build_auth_lockout_ui_context(
        locked=locked,
        retry_after_seconds=retry_after,
        message=_(
            "Too many incorrect admin MFA codes. Please try again in %(duration)s."
        ),
        prefix="admin_mfa_rate_limit",
    )
    response = render(
        request,
        "admin_mfa_verify.html",
        {
            "next": next_url,
            "admin_mfa_messages": admin_mfa_messages or [],
            "admin_mfa_timeout_active": remaining is not None,
            "admin_mfa_timeout_remaining_seconds": remaining,
            "admin_mfa_challenge_id": admin_mfa_challenge_id(request) or "",
            **rate_limit_context,
        },
    )
    return set_strict_no_cache_headers(response)


def _expire_admin_mfa_challenge(request, user, next_url: str, *, source: str):
    """Log expiry, clear the pending attempt, and return to the normal site."""
    log_auth_event(
        request,
        event_type="admin_mfa_verify_failure",
        success=False,
        user=user,
        username=user.get_username(),
        details={
            "reason": "admin_mfa_timeout",
            "source": source,
            "timeout_seconds": get_admin_mfa_verification_timeout_seconds(),
            "requested_next": next_url,
            "admin_step_up": True,
        },
    )
    clear_admin_mfa_challenge(request)
    _discard_pending_messages(request)
    response = redirect("home")
    return set_strict_no_cache_headers(response)


@require_POST
def start_admin_mfa_verification(request):
    """Start a fresh Admin MFA challenge through a CSRF-protected request."""
    user = getattr(request, "user", None)
    if not _is_admin_user(user):
        raise Http404()

    next_url = _safe_next_url(request)
    if not user_requires_mfa(user):
        _discard_pending_messages(request)
        messages.error(request, _("Admin access requires an active MFA-protected account."))
        return redirect("home")

    # Starting a new step-up attempt invalidates any older grant or pending
    # challenge, but keeps the normal authenticated Knowledge Repository session.
    clear_admin_mfa_session(request)
    locked, _retry_after, _identifier = get_auth_lockout_status(
        request,
        user=user,
        purpose="admin_mfa",
    )
    start_new_admin_mfa_challenge(request, defer_deadline=locked)
    response = redirect(_canonical_admin_mfa_verify_url(next_url))
    return set_strict_no_cache_headers(response)


def admin_mfa_verify(request):
    """Require a fresh TOTP check before entering Django Admin."""
    user = getattr(request, "user", None)
    if not _is_admin_user(user):
        raise Http404()

    next_url = _safe_next_url(request)

    if not user_requires_mfa(user):
        _discard_pending_messages(request)
        messages.error(request, _("Admin access requires an active MFA-protected account."))
        return redirect("home")

    device = getattr(user, "kb_mfa_device", None) or get_or_create_mfa_device(user)
    if not device.confirmed:
        _discard_pending_messages(request)
        messages.warning(request, _("Set up MFA before accessing the admin site."))
        return _redirect_with_next(
            "mfa_setup",
            reverse("admin_mfa_verify") + "?" + urlencode({"next": next_url}),
        )

    if admin_mfa_is_verified(request, user):
        _discard_pending_messages(request)
        return redirect(next_url)

    # Entering a protected Admin route starts a fresh challenge automatically,
    # then removes the one-time entry marker from the address bar. The user sees
    # the OTP field immediately instead of an intermediate start button. Normal
    # refreshes of the canonical verification URL keep the same fixed deadline.
    if request.method == "GET" and request.GET.get(ADMIN_MFA_ENTRY_PARAM) == "1":
        clear_admin_mfa_session(request)
        locked, _retry_after, _identifier = get_auth_lockout_status(
            request,
            user=user,
            purpose="admin_mfa",
        )
        start_new_admin_mfa_challenge(request, defer_deadline=locked)
        response = redirect(_canonical_admin_mfa_verify_url(next_url))
        return set_strict_no_cache_headers(response)

    if admin_mfa_challenge_id(request) is None:
        locked, _retry_after, _identifier = get_auth_lockout_status(
            request,
            user=user,
            purpose="admin_mfa",
        )
        start_new_admin_mfa_challenge(request, defer_deadline=locked)

    # Sessions left on the old explicit-retry state by an earlier deployment
    # are upgraded automatically when the verification page is next opened.
    if admin_mfa_challenge_is_expired_state(request):
        if request.method == "GET":
            locked, _retry_after, _identifier = get_auth_lockout_status(
                request,
                user=user,
                purpose="admin_mfa",
            )
            start_new_admin_mfa_challenge(request, defer_deadline=locked)
        else:
            return _expire_admin_mfa_challenge(
                request,
                user,
                next_url,
                source="legacy_expired_state",
            )

    if not mfa_device_secret_is_readable(device):
        log_auth_event(
            request,
            event_type="admin_mfa_verify_failure",
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

    # Pause or resume the short deadline before checking expiry. Therefore a
    # multi-minute lockout can never expire underneath the 60-second window.
    locked, retry_after, lockout_identifier = _prepare_admin_mfa_timing(request, user)
    action = (request.POST.get("action") or "").strip().lower()

    if request.method == "POST" and not admin_mfa_challenge_matches(
        request,
        request.POST.get("challenge_id"),
    ):
        return _render_admin_mfa_verify(request, next_url)

    if action == "timeout":
        if admin_mfa_challenge_has_expired(request):
            return _expire_admin_mfa_challenge(
                request,
                user,
                next_url,
                source="countdown",
            )
        return _render_admin_mfa_verify(request, next_url)

    if admin_mfa_challenge_has_expired(request):
        return _expire_admin_mfa_challenge(
            request,
            user,
            next_url,
            source="server_deadline",
        )

    admin_mfa_messages = []

    if request.method == "POST":
        if locked:
            defer_admin_mfa_deadline_for_lockout(request)
            log_auth_event(
                request,
                event_type="admin_mfa_verify_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details={
                    "reason": "temporary_lockout",
                    "lockout_identifier": lockout_identifier,
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

            # Rotate the authenticated session identifier before adding the
            # privileged Admin grant. Existing session data is preserved while
            # the old session key can no longer inherit the elevation.
            request.session.cycle_key()
            mark_admin_mfa_verified(request, user)
            log_auth_event(
                request,
                event_type="admin_mfa_verify_success",
                success=True,
                user=user,
                username=user.get_username(),
                details={"admin_step_up": True, "session_key_rotated": True},
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
                defer_admin_mfa_deadline_for_lockout(request)
                details["reason"] = "temporary_lockout_created"
                details["retry_after_seconds"] = lockout.get("retry_after_seconds")
                admin_mfa_messages.append(
                    _("Too many incorrect admin MFA codes. Please try again in %(duration)s.")
                    % {"duration": format_retry_after(lockout.get("retry_after_seconds"))}
                )

            log_auth_event(
                request,
                event_type="admin_mfa_verify_failure",
                success=False,
                user=user,
                username=user.get_username(),
                details=details,
            )
            admin_mfa_messages.append(_("Invalid authenticator code. Please try again."))

    return _render_admin_mfa_verify(request, next_url, admin_mfa_messages)

class AdminMFASessionMiddleware:
    """Require step-up MFA before Django Admin and sensitive admin tools.

    This does not log users out of the normal Knowledge Repository site. Leaving
    the protected admin scope clears only the short-lived admin-MFA grant. A
    protected route opens the OTP field automatically whenever a fresh step-up
    grant is required. Django Admin returns to the normal site after an idle
    timeout, while a standalone maintenance tool can immediately re-prompt for
    OTP and return to that tool after successful verification.
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

    def _is_django_admin_path(self, path: str) -> bool:
        return path == "/admin" or path.startswith("/admin/")

    def _is_static_or_safe_asset(self, path: str) -> bool:
        if settings.STATIC_URL and path.startswith(settings.STATIC_URL):
            return True
        media_url = getattr(settings, "MEDIA_URL", "")
        if media_url and path.startswith(media_url):
            return True
        return path in {"/favicon.ico", "/robots.txt"}

    def _clear_admin_mfa_when_leaving_admin(self, request, path: str) -> None:
        # The verified grant and any unfinished challenge are valid only while
        # the user remains in the Admin step-up flow. Keep challenge state on
        # the verification route itself so page refreshes cannot reset the fixed
        # countdown. Ignore static/media assets loaded by the Admin pages.
        if self._is_static_or_safe_asset(path):
            return
        verify_path = self._reverse_or_none("admin_mfa_verify")
        start_path = self._reverse_or_none("admin_mfa_start")
        if verify_path and (path == verify_path or path.startswith(verify_path + "/")):
            return
        if start_path and path == start_path:
            return
        if (
            request.session.get(ADMIN_MFA_VERIFIED_KEY)
            or request.session.get(ADMIN_MFA_CHALLENGE_ID_KEY)
            or request.session.get(ADMIN_MFA_CHALLENGE_STARTED_AT_KEY)
            or request.session.get(ADMIN_MFA_CHALLENGE_EXPIRES_AT_KEY)
            or request.session.get(ADMIN_MFA_CHALLENGE_EXPIRED_KEY)
            or request.session.get(ADMIN_MFA_LOCKOUT_DEFERRED_KEY)
        ):
            clear_admin_mfa_session(request)

    def _is_exempt_admin_path(self, path: str) -> bool:
        verify_path = self._reverse_or_none("admin_mfa_verify")
        start_path = self._reverse_or_none("admin_mfa_start")
        exempt_paths = {
            verify_path,
            start_path,
            "/admin/logout/",
            "/admin/jsi18n/",
        }
        return path in {p for p in exempt_paths if p}

    def _admin_last_activity_ts(self, request) -> int | None:
        try:
            return int(request.session.get(ADMIN_MFA_LAST_ACTIVITY_AT_KEY))
        except (TypeError, ValueError):
            return None

    def _admin_timeout_response(self, request, path: str):
        clear_admin_mfa_session(request)

        # A timed-out standalone maintenance tool should stay in its own flow:
        # ask for a fresh MFA code, then return directly to the requested tool.
        # Only Django Admin itself sends the user back to the normal site after
        # inactivity, preserving the existing admin-site timeout behaviour.
        if not self._is_django_admin_path(path):
            response = _redirect_with_next("admin_mfa_verify", request.get_full_path(), entry=True)
            return set_strict_no_cache_headers(response)

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
            # Explicitly discard any step-up grant before Django processes an
            # admin logout. Django normally clears the whole authenticated
            # session as well, but removing these keys here prevents a stale
            # grant from surviving any customised logout behaviour.
            if path == "/admin/logout/":
                clear_admin_mfa_session(request)
            return self.get_response(request)

        user = getattr(request, "user", None)
        if not _is_admin_user(user):
            return self.get_response(request)

        if not admin_mfa_is_verified(request, user):
            response = _redirect_with_next("admin_mfa_verify", request.get_full_path(), entry=True)
            return set_strict_no_cache_headers(response)

        now = _now_ts()
        last_activity = self._admin_last_activity_ts(request)
        if last_activity is None:
            request.session[ADMIN_MFA_LAST_ACTIVITY_AT_KEY] = now
            request.session.modified = True
        elif now - last_activity >= get_admin_mfa_idle_timeout_seconds():
            return self._admin_timeout_response(request, path)
        else:
            request.session[ADMIN_MFA_LAST_ACTIVITY_AT_KEY] = now
            request.session.modified = True

        response = self.get_response(request)
        return set_strict_no_cache_headers(response)
