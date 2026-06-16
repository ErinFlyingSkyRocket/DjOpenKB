#!/usr/bin/env python3
"""Idempotently enforce fresh Admin MFA when entering Django Admin.

Run from the project root. This script patches only small source snippets so it
can be safely run on top of an already-patched project without overwriting newer
local changes such as the 10-minute admin idle timeout setting.
"""
from pathlib import Path

ROOT = Path.cwd()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def ensure_settings_middleware() -> None:
    path = "djopenkb/settings.py"
    text = read(path)
    middleware = '    "kb.admin_security.AdminMFASessionMiddleware",\n'
    if "kb.admin_security.AdminMFASessionMiddleware" in text:
        return
    anchor = '    "kb.middleware.LocalMFARequiredMiddleware",\n'
    if anchor not in text:
        raise RuntimeError(f"Could not find LocalMFARequiredMiddleware in {path}")
    text = text.replace(anchor, anchor + middleware, 1)
    write(path, text)


def ensure_urls_route() -> None:
    path = "djopenkb/urls.py"
    text = read(path)
    if "admin_mfa_verify" not in text:
        import_anchor = "from django.urls import include, path\n"
        if import_anchor not in text:
            raise RuntimeError(f"Could not find django.urls import in {path}")
        text = text.replace(import_anchor, import_anchor + "from kb.admin_security import admin_mfa_verify\n", 1)
    if 'path("admin/mfa/verify/", admin_mfa_verify, name="admin_mfa_verify")' not in text:
        admin_anchor = '    path("admin/", admin.site.urls),\n'
        if admin_anchor not in text:
            raise RuntimeError(f"Could not find admin URL pattern in {path}")
        text = text.replace(
            admin_anchor,
            '    path("admin/mfa/verify/", admin_mfa_verify, name="admin_mfa_verify"),\n' + admin_anchor,
            1,
        )
    write(path, text)


def ensure_navbar_admin_link() -> None:
    path = "website/templates/_navbar.html"
    text = read(path)
    fresh_link = '<li><a href="{% url \'admin_mfa_verify\' %}?next=/admin/&fresh=1">{% trans "Admin" %}</a></li>'
    if fresh_link in text:
        return
    replacements = [
        '<li><a href="/admin/">{% trans "Admin" %}</a></li>',
        '<li><a href="{% url \'admin_mfa_verify\' %}?next=/admin/">{% trans "Admin" %}</a></li>',
        '<li><a href="{% url \'admin_mfa_verify\' %}?next=/admin">{% trans "Admin" %}</a></li>',
    ]
    for old in replacements:
        if old in text:
            text = text.replace(old, fresh_link, 1)
            write(path, text)
            return
    raise RuntimeError(f"Could not find Admin navbar link in {path}")


def ensure_admin_security_logic() -> None:
    path = "kb/admin_security.py"
    text = read(path)

    # 1) Fresh entry flag: /admin/mfa/verify/?fresh=1 clears old admin MFA token
    if 'force_fresh = (request.GET.get("fresh") == "1")' not in text:
        old = '    next_url = _safe_next_url(request)\n\n    if not user_requires_mfa(user):'
        new = '''    next_url = _safe_next_url(request)\n    force_fresh = (request.GET.get("fresh") == "1") or (request.POST.get("fresh") == "1")\n    if force_fresh:\n        # Entering Django Admin from the main site must require a fresh admin\n        # step-up challenge, even if an older admin-MFA token is still present.\n        clear_admin_mfa_session(request)\n\n    if not user_requires_mfa(user):'''
        if old not in text:
            raise RuntimeError(f"Could not insert fresh-entry Admin MFA logic in {path}")
        text = text.replace(old, new, 1)

    # 2) Add helpers that preserve admin token for static/media, but clear it
    # when a user leaves /admin/ for the main site.
    if "def _clear_admin_token_when_leaving_admin" not in text:
        old = '''    def _is_exempt_admin_path(self, path: str) -> bool:\n        verify_path = self._reverse_or_none("admin_mfa_verify")\n        exempt_paths = {\n            verify_path,\n            "/admin/logout/",\n            "/admin/jsi18n/",\n        }\n        return path in {p for p in exempt_paths if p}\n\n    def _admin_last_activity_ts(self, request) -> int | None:'''
        new = '''    def _is_exempt_admin_path(self, path: str) -> bool:\n        verify_path = self._reverse_or_none("admin_mfa_verify")\n        exempt_paths = {\n            verify_path,\n            "/admin/logout/",\n            "/admin/jsi18n/",\n        }\n        return path in {p for p in exempt_paths if p}\n\n    def _is_static_or_media_path(self, path: str) -> bool:\n        # Static/media requests may be triggered by admin pages. They must not\n        # clear the admin MFA token; otherwise admin CSS/JS/image loads could\n        # accidentally expire the step-up session.\n        static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"\n        media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"\n        return path.startswith(static_url) or path.startswith(media_url) or path == "/favicon.ico"\n\n    def _clear_admin_token_when_leaving_admin(self, request, path: str) -> None:\n        # When a superuser leaves Django Admin for the main Knowledge Repository\n        # site, clear only the admin step-up token. The normal login session stays\n        # active. This makes every later admin entry require MFA again.\n        user = getattr(request, "user", None)\n        if self._is_admin_path(path) or self._is_static_or_media_path(path):\n            return\n        if admin_mfa_is_verified(request, user):\n            clear_admin_mfa_session(request)\n\n    def _admin_last_activity_ts(self, request) -> int | None:'''
        if old not in text:
            raise RuntimeError(f"Could not insert admin-leave token clearing helpers in {path}")
        text = text.replace(old, new, 1)

    # 3) Change non-admin path handling so leaving admin clears only the admin token.
    old = '''    def __call__(self, request):\n        path = request.path_info or request.path\n        if not self._is_admin_path(path) or self._is_exempt_admin_path(path):\n            return self.get_response(request)\n\n        user = getattr(request, "user", None)'''
    new = '''    def __call__(self, request):\n        path = request.path_info or request.path\n        if not self._is_admin_path(path):\n            self._clear_admin_token_when_leaving_admin(request, path)\n            return self.get_response(request)\n\n        if self._is_exempt_admin_path(path):\n            return self.get_response(request)\n\n        user = getattr(request, "user", None)'''
    if old in text:
        text = text.replace(old, new, 1)
    elif "self._clear_admin_token_when_leaving_admin(request, path)" not in text:
        raise RuntimeError(f"Could not patch AdminMFASessionMiddleware.__call__ in {path}")

    write(path, text)


def main() -> None:
    ensure_settings_middleware()
    ensure_urls_route()
    ensure_navbar_admin_link()
    ensure_admin_security_logic()
    print("Admin MFA every-entry enforcement patch applied successfully.")


if __name__ == "__main__":
    main()
