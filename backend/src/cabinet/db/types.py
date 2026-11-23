"""Custom SQLAlchemy column types."""

from __future__ import annotations

from sqlalchemy.types import String, TypeDecorator

from cabinet.utils.secrets import decrypt_secret, encrypt_secret


class EncryptedString(TypeDecorator):
    """Transparently encrypt/decrypt a text column with the app key.

    Without ``CABINET_ENCRYPTION_KEY`` values pass through, so dev/test
    databases and legacy plaintext rows keep working. New writes with a key
    set are stored as ``enc:v1:<fernet token>``.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        return decrypt_secret(value)
