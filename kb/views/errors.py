from __future__ import annotations

from urllib.parse import urlparse

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _


_ERROR_STATUS_CODES = {400, 403, 404, 405, 413, 429, 500, 502, 503, 504}


def _safe_back_url(request: HttpRequest) -> str:
    """Return a same-host referrer, or a sensible application landing page."""
    referer = request.META.get("HTTP_REFERER", "")
    if referer:
        parsed = urlparse(referer)
        if not parsed.netloc or parsed.netloc == request.get_host():
            # Avoid sending the user back to the same erroring URL forever.
            current_url = request.build_absolute_uri()
            if referer != current_url:
                return referer

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return reverse("home")
    return reverse("root_login")


def _error_content(status_code: int) -> tuple[str, str, str]:
    """Return translated title, message, and guidance for an HTTP error."""
    content = {
        400: (
            _("Bad request"),
            _("The request could not be processed."),
            _("Please check the submitted information and try again."),
        ),
        403: (
            _("Access denied"),
            _("You do not have permission to access this page or perform this action."),
            _("Return to the previous page or contact an administrator if access is required."),
        ),
        404: (
            _("Page not found"),
            _("The page you requested could not be found."),
            _("The link may be invalid, expired, or you may not have access to this page."),
        ),
        405: (
            _("Request not allowed"),
            _("This type of request is not allowed for the selected page."),
            _("Return to the previous page and use the available controls instead."),
        ),
        413: (
            _("Request too large"),
            _("The submitted file or request is larger than the permitted limit."),
            _("Reduce the file size or request content, then try again."),
        ),
        429: (
            _("Too many requests"),
            _("Too many requests were submitted within a short period of time."),
            _("Please wait briefly before trying again."),
        ),
        500: (
            _("Server error"),
            _("The server could not complete your request."),
            _("Please try again later or contact an administrator if the issue continues."),
        ),
        502: (
            _("Service unavailable"),
            _("The application service is temporarily unavailable."),
            _("Please try again shortly."),
        ),
        503: (
            _("Service unavailable"),
            _("The application is temporarily unable to handle the request."),
            _("Please try again shortly."),
        ),
        504: (
            _("Request timed out"),
            _("The application took too long to respond."),
            _("Please wait briefly and try again."),
        ),
    }
    return content.get(status_code, content[500])


def render_http_error(
    request: HttpRequest,
    status_code: int,
    *,
    page_title: str | None = None,
    error_message: str | None = None,
    error_help: str | None = None,
) -> HttpResponse:
    """Render the shared friendly HTML error page with defensive headers."""
    if status_code not in _ERROR_STATUS_CODES:
        status_code = 500

    default_title, default_message, default_help = _error_content(status_code)
    response = render(
        request,
        "error.html",
        context={
            "page_title": page_title or default_title,
            "error_code": status_code,
            "error_message": error_message or default_message,
            "error_help": error_help or default_help,
            "safe_back_url": _safe_back_url(request),
        },
        status=status_code,
    )

    # Error pages can contain authentication-aware navigation and must never be
    # cached or indexed. The CSP middleware still adds the normal nonce-based
    # site policy to Django-rendered responses.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noimageindex"

    if status_code == 429:
        # Nginx's strictest configured request bucket refills within 30 seconds.
        response["Retry-After"] = "30"
    elif status_code in {502, 503, 504}:
        response["Retry-After"] = "60"

    return response


def bad_request(request: HttpRequest, exception: Exception) -> HttpResponse:
    """Friendly HTTP 400 response."""
    return render_http_error(request, 400)


def permission_denied(request: HttpRequest, exception: Exception) -> HttpResponse:
    """Friendly HTTP 403 response."""
    return render_http_error(request, 403)


def page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    """Generic 404 page that does not reveal whether a resource exists."""
    return render_http_error(request, 404)


def server_error(request: HttpRequest) -> HttpResponse:
    """Friendly HTTP 500 response."""
    return render_http_error(request, 500)


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """Use the shared 403 layout for failed or expired CSRF submissions."""
    return render_http_error(
        request,
        403,
        error_message=_("The request could not be verified."),
        error_help=_("Refresh the page and try again. If the issue continues, sign in again."),
    )
