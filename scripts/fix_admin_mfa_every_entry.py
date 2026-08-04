#!/usr/bin/env python3
"""Compatibility check for the automatic Admin MFA OTP-entry flow.

Modern DjOpenKB versions send protected Django Admin and administrator-tool
requests to the Admin MFA verification route. The route creates a fresh,
fixed-deadline challenge automatically and displays the OTP field without an
intermediate "start verification" button.

This script does not rewrite project files. It only checks that the automatic
flow is present, preventing older maintenance instructions from restoring the
retired POST-button workflow.
"""
from pathlib import Path

ROOT = Path.cwd()


def contains(path: str, marker: str) -> bool:
    candidate = ROOT / path
    return candidate.exists() and marker in candidate.read_text(encoding="utf-8")


def excludes(path: str, marker: str) -> bool:
    candidate = ROOT / path
    return candidate.exists() and marker not in candidate.read_text(encoding="utf-8")


def main() -> None:
    checks = {
        "automatic Admin MFA challenge entry": contains(
            "kb/admin_security.py",
            "The user sees\n    # the OTP field immediately instead of an intermediate start button.",
        ),
        "direct navbar Admin link": contains(
            "website/templates/_navbar.html",
            "{% url 'admin:index' %}",
        ),
        "OTP field on the Admin MFA page": contains(
            "website/templates/admin_mfa_verify.html",
            'id="id_code"',
        ),
        "retired start button absent": excludes(
            "website/templates/admin_mfa_verify.html",
            "Start new verification window",
        ),
        "retired navbar start form absent": excludes(
            "website/templates/_navbar.html",
            "{% url 'admin_mfa_start' %}",
        ),
    }
    missing = [label for label, present in checks.items() if not present]
    if missing:
        raise RuntimeError(
            "The automatic Admin MFA OTP-entry update is incomplete: "
            + ", ".join(missing)
            + ". Apply the latest incremental project files."
        )

    print("Automatic Admin MFA OTP-entry flow is installed; no changes made.")


if __name__ == "__main__":
    main()
