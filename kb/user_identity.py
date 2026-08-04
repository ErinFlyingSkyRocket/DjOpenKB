"""Shared user identity validation for local and LDAP-managed accounts."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext as _


def first_ldap_attribute(ldap_user, attribute_name):
    """Return the first non-empty LDAP attribute value as text."""
    attrs = getattr(ldap_user, "attrs", {}) or {}
    values = attrs.get(attribute_name) or attrs.get(attribute_name.lower()) or []
    if isinstance(values, (str, bytes)):
        values = [values]
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        value = str(value or "").strip()
        if value:
            return value
    return ""


def authoritative_ldap_username(ldap_user):
    """Return the AD sAMAccountName used as the Django username."""
    return first_ldap_attribute(ldap_user, "sAMAccountName").lower()


def authoritative_ldap_email(ldap_user):
    """Return the directory mail value, normalized for comparison."""
    return first_ldap_attribute(ldap_user, "mail").strip()


def validate_unique_user_email(raw_email, *, user=None, required=False):
    """Validate one User email and enforce case-insensitive uniqueness.

    The database also has a partial unique index for non-blank emails. This
    helper provides a friendly error before the database constraint is reached.
    """
    email = (raw_email or "").strip()
    UserModel = get_user_model()
    email_field = UserModel._meta.get_field("email")
    max_length = int(getattr(email_field, "max_length", 254) or 254)

    if not email:
        if required:
            raise ValidationError(validate_email.message, code="invalid")
        return ""

    if len(email) > max_length:
        raise ValidationError(validate_email.message, code="invalid")

    validate_email(email)

    queryset = UserModel._default_manager.filter(email__iexact=email)
    if user is not None and getattr(user, "pk", None):
        queryset = queryset.exclude(pk=user.pk)
    if queryset.exists():
        raise ValidationError(
            _("Please check the submitted information and try again."),
            code="duplicate",
        )

    return email
