from __future__ import annotations

from urllib.parse import urlparse

from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _


def _safe_back_url(request):
    """Return a same-host referrer, or a sensible app landing page."""
    referer = request.META.get("HTTP_REFERER", "")
    if referer:
        parsed = urlparse(referer)
        if not parsed.netloc or parsed.netloc == request.get_host():
            # Avoid sending the user back to the same missing URL forever.
            current_url = request.build_absolute_uri()
            if referer != current_url:
                return referer

    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        return reverse("home")
    return reverse("root_login")


def page_not_found(request, exception):
    """Generic 404 page that does not reveal whether a protected resource exists."""
    context = {
        "page_title": _("Page not found"),
        "safe_back_url": _safe_back_url(request),
        "show_home_button": bool(getattr(request, "user", None) and request.user.is_authenticated),
    }
    return render(request, "404.html", context=context, status=404)
