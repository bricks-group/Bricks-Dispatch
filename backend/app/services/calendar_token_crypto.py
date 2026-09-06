"""App-level encryption for vendor calendar secrets at rest.

Env: CALENDAR_TOKEN_ENCRYPTION_KEY on Vercel dispatch-api (Fernet key or any secret;
SHA-256 is used to derive a Fernet key when the value is not already a Fernet key).

Fail-soft: if the key is missing, writes stay plaintext and encrypted reads return
None so FreeBusy / auto-assign treats the calendar as unknown — never break assign.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"
SECRET_FIELDS = ("access_token", "refresh_token", "caldav_password")


def calendar_encryption_key() -> str:
    return (os.environ.get("CALENDAR_TOKEN_ENCRYPTION_KEY") or "").strip()


def encryption_configured() -> bool:
    return bool(calendar_encryption_key())


def _fernet():
    raw = calendar_encryption_key()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception as e:  # pragma: no cover
        logger.warning("cryptography.Fernet unavailable: %s", type(e).__name__)
        return None
    # Accept a standard Fernet key, else derive one from the env secret.
    try:
        return Fernet(raw.encode("utf-8") if not isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt for storage. Pass-through when already encrypted or key missing."""
    if plaintext is None:
        return None
    if plaintext == "":
        return ""
    if is_encrypted(plaintext):
        return plaintext
    f = _fernet()
    if f is None:
        return plaintext
    token = f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return PREFIX + token


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """Decrypt for FreeBusy. Plaintext legacy values pass through.

    Missing key + ciphertext → None (fail soft / unknown calendar).
    """
    if value is None:
        return None
    if value == "":
        return ""
    if not is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        logger.warning("CALENDAR_TOKEN_ENCRYPTION_KEY missing — cannot decrypt calendar secret")
        return None
    try:
        return f.decrypt(value[len(PREFIX) :].encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.warning("calendar secret decrypt failed: %s", type(e).__name__)
        return None


def encrypt_connection_secrets(row: dict) -> dict:
    """Return a shallow copy with secret fields encrypted when a key is configured."""
    out = dict(row or {})
    if not encryption_configured():
        return out
    for field in SECRET_FIELDS:
        if field in out and out[field]:
            out[field] = encrypt_secret(out[field])
    return out


def decrypt_connection_secrets(row: dict) -> dict:
    """Return a shallow copy with secret fields decrypted for FreeBusy use."""
    out = dict(row or {})
    for field in SECRET_FIELDS:
        if field in out and out[field]:
            out[field] = decrypt_secret(out[field])
    return out


def needs_reencrypt(row: dict) -> bool:
    """True when key is set and any secret field is still plaintext."""
    if not encryption_configured():
        return False
    for field in SECRET_FIELDS:
        val = row.get(field)
        if val and not is_encrypted(val):
            return True
    return False
