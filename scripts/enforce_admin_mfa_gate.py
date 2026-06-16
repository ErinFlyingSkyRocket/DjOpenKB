#!/usr/bin/env python3
"""Idempotently enforce the Django Admin MFA gate.

Run from the Django project root:

    python3 scripts/enforce_admin_mfa_gate.py

This fixes three common wiring gaps:
1. /admin/mfa/verify/ route is registered before /admin/.
2. AdminMFASessionMiddleware is installed before ForceLoginAndAdminGuardMiddleware.
3. The navbar Admin link points to the MFA gate instead of directly to /admin/.
4. ForceLoginAndAdminGuardMiddleware has a defensive /admin/ -> MFA redirect.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.cwd()


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def ensure_settings() -> None:
    path = ROOT / "djopenkb" / "settings.py"
    text = read(path)
    nl = detect_newline(text)
    middleware_line = '    "kb.admin_security.AdminMFASessionMiddleware",'
    force_line = '    "kb.middleware.ForceLoginAndAdminGuardMiddleware",'

    # Remove duplicate/old placements, then insert at the correct position.
    lines = [line for line in text.splitlines() if line.strip() != middleware_line.strip()]
    text = nl.join(lines) + (nl if text.endswith(("\n", "\r\n")) else "")

    if force_line not in text:
        raise SystemExit("Could not find ForceLoginAndAdminGuardMiddleware in djopenkb/settings.py")
    text = text.replace(force_line, middleware_line + nl + force_line, 1)
    write(path, text)
    print("Updated djopenkb/settings.py: AdminMFASessionMiddleware placed before ForceLoginAndAdminGuardMiddleware")


def ensure_urls() -> None:
    path = ROOT / "djopenkb" / "urls.py"
    text = read(path)
    nl = detect_newline(text)

    import_line = "from kb.admin_security import admin_mfa_verify"
    if import_line not in text:
        pattern = re.compile(r"^from django\.urls import include, path$", re.MULTILINE)
        if not pattern.search(text):
            raise SystemExit("Could not find real django.urls import line in djopenkb/urls.py")
        text = pattern.sub("from django.urls import include, path" + nl + nl + import_line, text, count=1)

    route_line = '    path("admin/mfa/verify/", admin_mfa_verify, name="admin_mfa_verify"),'
    # Remove duplicates, then insert before Django's admin route.
    text = nl.join([line for line in text.splitlines() if line.strip() != route_line.strip()]) + (nl if text.endswith(("\n", "\r\n")) else "")
    admin_route = '    path("admin/", admin.site.urls),'
    if admin_route not in text:
        raise SystemExit("Could not find admin.site.urls route in djopenkb/urls.py")
    text = text.replace(admin_route, route_line + nl + admin_route, 1)

    write(path, text)
    print("Updated djopenkb/urls.py: /admin/mfa/verify/ registered before /admin/")


def ensure_navbar() -> None:
    path = ROOT / "website" / "templates" / "_navbar.html"
    text = read(path)
    original = text

    direct_admin_link = '<li><a href="/admin/">{% trans "Admin" %}</a></li>'
    gated_admin_link = '<li><a href="{% url \'admin_mfa_verify\' %}?next=/admin/">{% trans "Admin" %}</a></li>'
    text = text.replace(direct_admin_link, gated_admin_link)

    # Hide the admin navbar item unless Django Admin access is really possible.
    # Future notification/non-admin groups can still use other permissions, but the
    # Django Admin link should be superuser-only.
    text = text.replace(
        "{% if user|can_use_admin_tools %}" + "\n" + "                        " + gated_admin_link,
        "{% if user.is_superuser %}" + "\n" + "                        " + gated_admin_link,
    )

    if text != original:
        write(path, text)
        print("Updated website/templates/_navbar.html: Admin link now uses admin MFA gate")
    else:
        print("Navbar already appears to use the admin MFA gate, or no direct /admin/ link was found")


def ensure_middleware_defensive_redirect() -> None:
    path = ROOT / "kb" / "middleware.py"
    text = read(path)
    nl = detect_newline(text)

    marker = "# ADMIN_MFA_GATE_ENFORCEMENT_START"
    if marker in text:
        print("kb/middleware.py already has defensive admin MFA redirect")
        return

    # Match the admin-path block inside ForceLoginAndAdminGuardMiddleware. This
    # supports both the old permission helper check and the newer superuser check.
    pattern = re.compile(
        r"(?P<indent> {12})if self\._is_admin_path\(path\):\n"
        r"(?P<body>(?: {16}.+\n)+?)"
        r"(?P<next> {12})return self\.get_response\(request\)",
        re.MULTILINE,
    )

    match = pattern.search(text)
    if not match:
        raise SystemExit("Could not locate admin path block inside ForceLoginAndAdminGuardMiddleware in kb/middleware.py")

    indent = match.group("indent")
    new_block = f'''{indent}if self._is_admin_path(path):
{indent}    # ADMIN_MFA_GATE_ENFORCEMENT_START
{indent}    admin_mfa_verify_path = self._reverse_or_none("admin_mfa_verify")
{indent}    is_admin_mfa_verify_path = bool(admin_mfa_verify_path and path == admin_mfa_verify_path)

{indent}    if not (getattr(user, "is_staff", False) and getattr(user, "is_superuser", False)):
{indent}        raise Http404()
{indent}    if not _admin_cidr_allowed(request):
{indent}        raise Http404()

{indent}    if not is_admin_mfa_verify_path:
{indent}        from .admin_security import admin_mfa_is_verified

{indent}        if not admin_mfa_is_verified(request, user):
{indent}            if not admin_mfa_verify_path:
{indent}                raise Http404()
{indent}            response = redirect(f"{{admin_mfa_verify_path}}?{{urlencode({{'next': request.get_full_path()}})}}")
{indent}            return set_strict_no_cache_headers(response)
{indent}    # ADMIN_MFA_GATE_ENFORCEMENT_END
{indent}return self.get_response(request)'''

    text = text[: match.start()] + new_block + text[match.end():]
    write(path, text)
    print("Updated kb/middleware.py: direct /admin/ access now redirects to admin MFA gate")


def main() -> None:
    ensure_settings()
    ensure_urls()
    ensure_navbar()
    ensure_middleware_defensive_redirect()
    print("Admin MFA gate enforcement completed.")


if __name__ == "__main__":
    main()
