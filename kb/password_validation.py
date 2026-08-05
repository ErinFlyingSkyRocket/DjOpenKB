"""Shared password policy for every Django-managed local account.

The validator is registered through ``AUTH_PASSWORD_VALIDATORS`` so the same
rules apply to self-service password changes, Django Admin user creation, and
Django Admin password changes. Active Directory managed accounts remain subject
to the domain password policy instead.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _, ngettext


class KnowledgeRepositoryPasswordValidator:
    """Require one consistent strong-password policy across the project."""

    def __init__(self, min_length: int = 12):
        self.min_length = max(int(min_length), 12)

    def validate(self, password: str, user=None) -> None:
        password = password or ""
        errors: list[ValidationError] = []

        if len(password) < self.min_length:
            errors.append(
                ValidationError(
                    ngettext(
                        "Password must be at least %(min_length)d character long.",
                        "Password must be at least %(min_length)d characters long.",
                        self.min_length,
                    ),
                    code="password_too_short",
                    params={"min_length": self.min_length},
                )
            )
        if not re.search(r"[A-Z]", password):
            errors.append(
                ValidationError(
                    _("Password must include at least 1 uppercase letter."),
                    code="password_no_uppercase",
                )
            )
        if not re.search(r"[a-z]", password):
            errors.append(
                ValidationError(
                    _("Password must include at least 1 lowercase letter."),
                    code="password_no_lowercase",
                )
            )
        if not re.search(r"[0-9]", password):
            errors.append(
                ValidationError(
                    _("Password must include at least 1 number."),
                    code="password_no_number",
                )
            )
        if not re.search(r"[^A-Za-z0-9]", password):
            errors.append(
                ValidationError(
                    _("Password must include at least 1 special character."),
                    code="password_no_special_character",
                )
            )

        lower_password = password.casefold()
        if user is not None:
            username = str(getattr(user, "get_username", lambda: "")() or "").casefold()
            email_name = str(getattr(user, "email", "") or "").split("@", 1)[0].casefold()

            if username and len(username) >= 3 and username in lower_password:
                errors.append(
                    ValidationError(
                        _("Password must not contain your username."),
                        code="password_contains_username",
                    )
                )
            if email_name and len(email_name) >= 3 and email_name in lower_password:
                errors.append(
                    ValidationError(
                        _("Password must not contain the name part of your email address."),
                        code="password_contains_email_name",
                    )
                )

        if errors:
            raise ValidationError(errors)

    def get_help_text(self) -> str:
        return _(
            "Use at least %(min_length)d characters with uppercase, lowercase, "
            "a number, and a special character. Do not include your username or "
            "the name part of your email address."
        ) % {"min_length": self.min_length}
