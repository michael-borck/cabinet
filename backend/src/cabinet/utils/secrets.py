"""At-rest encryption for stored API keys (Phase 6 / ship hardening).

Electron main generates a random 32-byte key, protects it with
``safeStorage`` (the OS keychain), and passes it to the backend as
``CABINET_ENCRYPTION_KEY``. The ``EncryptedString`` column type uses Fernet so
the SQLite file only ever contains ciphertext.

Standalone/dev runs (``uv run uvicorn``, tests) normally have no key; values
then pass through unchanged, which keeps local development frictionless.
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

CIPHER_PREFIX = "enc:v1:"
_ENV_VAR = "CABINET_ENCRYPTION_KEY"


def encryption_available() -> bool:
    return _fernet() is not None


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(CIPHER_PREFIX))


def encrypt_secret(value: str | None) -> str | None:
    if value is None or is_encrypted(value):
        return value
    fernet = _fernet()
    if fernet is None:
        return value
    token = fernet.encrypt(value.encode("utf-8")).decode("ascii")
    return f"{CIPHER_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None or not is_encrypted(value):
        return value
    fernet = _fernet()
    if fernet is None:
        logger.warning(
            "Encrypted API key found but %s is not set; the provider will "
            "receive ciphertext",
            _ENV_VAR,
        )
        return value
    try:
        token = value[len(CIPHER_PREFIX) :]
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning(
            "Could not decrypt a stored API key (wrong %s?)", _ENV_VAR
        )
        return value


def _fernet() -> Fernet | None:
    raw_key = os.environ.get(_ENV_VAR)
    if not raw_key:
        return None
    try:
        raw = bytes.fromhex(raw_key)
    except ValueError:
        try:
            raw = base64.urlsafe_b64decode(raw_key)
        except Exception:
            logger.warning("Invalid %s; API keys will not be encrypted", _ENV_VAR)
            return None
    if len(raw) != 32:
        logger.warning(
            "%s must be 32 bytes (64 hex chars); API keys will not be encrypted",
            _ENV_VAR,
        )
        return None
    return Fernet(base64.urlsafe_b64encode(raw))
