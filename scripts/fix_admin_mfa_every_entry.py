#!/usr/bin/env python3
"""Legacy compatibility check for the Admin MFA every-entry update.

Modern DjOpenKB versions start a new Admin MFA challenge only through the
CSRF-protected ``admin_mfa_start`` POST endpoint. The older ``fresh=1`` GET
patch is intentionally retired because a GET request must not rotate or clear
security-sensitive session state.

This script no longer rewrites project files. It only confirms that the modern
flow is present, preventing an accidental rerun from restoring the old GET
behaviour.
"""
from pathlib import Path

ROOT = Path.cwd()


def contains(path: str, marker: str) -> bool:
    candidate = ROOT / path
    return candidate.exists() and marker in candidate.read_text(encoding="utf-8")


def main() -> None:
    checks = {
        "CSRF-protected Admin MFA start view": contains(
            "kb/admin_security.py",
            "def start_admin_mfa_verification",
        ),
        "Admin MFA start URL": contains(
            "djopenkb/urls.py",
            'name="admin_mfa_start"',
        ),
        "POST-based navbar Admin action": contains(
            "website/templates/_navbar.html",
            "{% url 'admin_mfa_start' %}",
        ),
    }
    missing = [label for label, present in checks.items() if not present]
    if missing:
        raise RuntimeError(
            "The current POST-based Admin MFA update is incomplete: "
            + ", ".join(missing)
            + ". Apply the latest incremental project files instead of the retired fresh=1 patch."
        )

    print("Modern POST-based Admin MFA every-entry flow is already installed; no changes made.")


if __name__ == "__main__":
    main()
