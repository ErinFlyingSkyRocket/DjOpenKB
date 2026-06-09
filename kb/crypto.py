"""Application-level encryption helpers for sensitive database fields.

Django already hashes user passwords, but TOTP authenticator secrets must be
recoverable for verification. These helpers encrypt recoverable secrets before
saving them to the database.
"""

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

ENCRYPTED_VALUE_PREFIX = "fernet$"


def _derive_fernet_key(key_material: str) -> bytes:
    """Convert arbitrary secret text into a Fernet-compatible 32-byte key."""
    digest = hashlib.sha256((key_material or "").encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def get_field_fernet() -> Fernet:
    """Return the Fernet instance used for sensitive model fields.

    Prefer setting DJANGO_FIELD_ENCRYPTION_KEY in Vault. If it is not set, the
    project falls back to DJANGO_SECRET_KEY so existing deployments do not break.
    Do not rotate this key unless you first re-encrypt existing database values.
    """
    key_material = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or getattr(settings, "SECRET_KEY", "")
    if not key_material:
        raise RuntimeError("FIELD_ENCRYPTION_KEY or SECRET_KEY must be configured before encrypted fields can be used.")
    return Fernet(_derive_fernet_key(str(key_material)))


def is_encrypted_value(value: str) -> bool:
    return str(value or "").startswith(ENCRYPTED_VALUE_PREFIX)


def encrypt_value(value: str) -> str:
    """Encrypt plain text unless it is already encrypted."""
    value = str(value or "")
    if not value or is_encrypted_value(value):
        return value
    token = get_field_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return ENCRYPTED_VALUE_PREFIX + token


def decrypt_value(value: str) -> str:
    """Decrypt an encrypted value; return legacy plaintext unchanged.

    Returning legacy plaintext keeps old MFA devices working until the migration
    or the next save encrypts them.
    """
    value = str(value or "")
    if not value:
        return ""
    if not is_encrypted_value(value):
        return value

    token = value[len(ENCRYPTED_VALUE_PREFIX):]
    try:
        return get_field_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("Unable to decrypt an encrypted database field. The field encryption key may have changed.")
        return ""
