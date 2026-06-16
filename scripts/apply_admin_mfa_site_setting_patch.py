#!/usr/bin/env python3
"""Idempotently enable Admin MFA step-up URL and middleware.

Run from the Django project root after extracting the patch files:

    python3 scripts/apply_admin_mfa_site_setting_patch.py

The script only edits djopenkb/settings.py and djopenkb/urls.py. It keeps existing
content and is safe to run more than once.
"""

from pathlib import Path

ROOT = Path.cwd()


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def update_settings() -> None:
    path = ROOT / "djopenkb" / "settings.py"
    text = path.read_text()
    nl = detect_newline(text)
    line = '    "kb.admin_security.AdminMFASessionMiddleware",'
    if line in text:
        print("settings.py already has AdminMFASessionMiddleware")
        return
    anchor = '    "kb.middleware.LocalMFARequiredMiddleware",'
    if anchor not in text:
        raise SystemExit("Could not find LocalMFARequiredMiddleware in djopenkb/settings.py")
    text = text.replace(anchor, anchor + nl + line, 1)
    path.write_text(text)
    print("Updated djopenkb/settings.py")


def update_urls() -> None:
    path = ROOT / "djopenkb" / "urls.py"
    text = path.read_text()
    nl = detect_newline(text)

    import_line = "from kb.admin_security import admin_mfa_verify"
    if import_line not in text:
        anchor = "from django.urls import include, path"
        if anchor not in text:
            raise SystemExit("Could not find django.urls import line in djopenkb/urls.py")
        text = text.replace(anchor, anchor + nl + nl + import_line, 1)

    route_line = '    path("admin/mfa/verify/", admin_mfa_verify, name="admin_mfa_verify"),'
    if route_line not in text:
        anchor = '    path("admin/", admin.site.urls),'
        if anchor not in text:
            raise SystemExit("Could not find admin.site.urls route in djopenkb/urls.py")
        text = text.replace(anchor, route_line + nl + anchor, 1)

    path.write_text(text)
    print("Updated djopenkb/urls.py")


if __name__ == "__main__":
    update_settings()
    update_urls()
    print("Admin MFA step-up wiring completed.")
