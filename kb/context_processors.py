from urllib.parse import urlsplit

from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme


_SKIP_BACK_PATH_PREFIXES = (
    "/logout",
)


def _safe_local_url(request, candidate):
    """Return a same-host relative URL, or an empty string if unsafe.

    Back links may come from a ?next= value or the HTTP Referer header. Both
    are user-controllable inputs, so only same-host URLs are allowed and the
    final value is normalised back to a local path before templates render it.
    """
    candidate = (candidate or "").strip()
    if not candidate:
        return ""

    if not url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ""

    parsed = urlsplit(candidate)
    path = parsed.path or "/"
    if any(path.startswith(prefix) for prefix in _SKIP_BACK_PATH_PREFIXES):
        return ""

    local_url = path
    if parsed.query:
        local_url += "?" + parsed.query
    if parsed.fragment:
        local_url += "#" + parsed.fragment

    current_url = request.get_full_path()
    if local_url == current_url:
        return ""

    return local_url


def safe_back(request):
    """Expose a safe global back URL to all templates.

    Priority:
    1. Safe ?next= URL, because edit/admin flows already pass this on purpose.
    2. Safe same-site HTTP Referer.
    3. Home page fallback.
    """
    try:
        fallback_url = reverse("home")
    except NoReverseMatch:
        fallback_url = "/"

    safe_back_url = (
        _safe_local_url(request, request.GET.get("next"))
        or _safe_local_url(request, request.POST.get("next"))
        or _safe_local_url(request, request.META.get("HTTP_REFERER"))
        or fallback_url
    )

    current_url = request.get_full_path()
    show_global_back_button = bool(safe_back_url and safe_back_url != current_url)

    return {
        "safe_back_url": safe_back_url,
        "show_global_back_button": show_global_back_button,
    }
