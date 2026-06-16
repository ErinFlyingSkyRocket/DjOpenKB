#!/usr/bin/env python3
"""Patch Admin MFA idle timeout defaults from 30 minutes to 10 minutes.

Run from the project root after extracting this patch package:
    python3 scripts/apply_admin_mfa_10min_default_patch.py

The script only performs targeted text replacements and is safe to run more than once.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REPLACEMENTS = {
    "kb/models.py": [
        ("admin_mfa_idle_timeout_seconds = models.PositiveIntegerField(\n        default=1800,", "admin_mfa_idle_timeout_seconds = models.PositiveIntegerField(\n        default=600,"),
        ("Default is 1800 seconds (30 minutes). Minimum enforced by code is 60 seconds; maximum enforced by code is 86400 seconds.", "Default is 600 seconds (10 minutes). Minimum enforced by code is 60 seconds; maximum enforced by code is 86400 seconds."),
    ],
    "kb/admin_security.py": [
        ("available. Default: 1800 seconds / 30 minutes.", "available. Default: 600 seconds / 10 minutes."),
        ('getattr(settings, "ADMIN_MFA_IDLE_TIMEOUT_SECONDS", 1800)', 'getattr(settings, "ADMIN_MFA_IDLE_TIMEOUT_SECONDS", 600)'),
        ("# unavailable during startup. Keep the safe 30-minute fallback.", "# unavailable during startup. Keep the safe 10-minute fallback."),
        ("value = 1800", "value = 600"),
    ],
}

MIGRATION_SOURCE = ROOT / "kb" / "migrations" / "0010_admin_mfa_idle_timeout_default_10min.py"
PATCH_MIGRATION_SOURCE = ROOT / "admin_mfa_10min_patch" / "kb" / "migrations" / "0010_admin_mfa_idle_timeout_default_10min.py"


def patch_file(relative_path: str, replacements: list[tuple[str, str]]) -> None:
    path = ROOT / relative_path
    if not path.exists():
        print(f"[skip] {relative_path} not found")
        return

    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        text = text.replace(old, new)

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"[updated] {relative_path}")
    else:
        print(f"[ok] {relative_path} already patched or pattern not present")


def ensure_migration() -> None:
    target = ROOT / "kb" / "migrations" / "0010_admin_mfa_idle_timeout_default_10min.py"
    # If script is copied into project root, the migration may already be present from zip extraction.
    bundled = ROOT / "admin_mfa_10min_patch" / "kb" / "migrations" / "0010_admin_mfa_idle_timeout_default_10min.py"
    if target.exists():
        print("[ok] migration already exists")
        return
    if bundled.exists():
        target.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
        print("[created] kb/migrations/0010_admin_mfa_idle_timeout_default_10min.py")
        return
    print("[warn] bundled migration source not found; please copy the migration file manually")


def main() -> None:
    for rel, replacements in REPLACEMENTS.items():
        patch_file(rel, replacements)
    ensure_migration()
    print("Done. Now run: sudo docker compose up -d --build && sudo docker compose exec web python manage.py migrate")


if __name__ == "__main__":
    main()
