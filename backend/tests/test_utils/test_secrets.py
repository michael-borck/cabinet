"""At-rest secret helpers (CABINET_ENCRYPTION_KEY)."""

from cabinet.utils import secrets

KEY = "11" * 32
OTHER_KEY = "22" * 32


def test_identity_without_a_key(monkeypatch):
    monkeypatch.delenv("CABINET_ENCRYPTION_KEY", raising=False)
    assert secrets.encrypt_secret("abc") == "abc"
    assert secrets.decrypt_secret("abc") == "abc"
    assert not secrets.is_encrypted("abc")


def test_round_trip_and_idempotence(monkeypatch):
    monkeypatch.setenv("CABINET_ENCRYPTION_KEY", KEY)

    token = secrets.encrypt_secret("sk-secret")

    assert token is not None
    assert token.startswith(secrets.CIPHER_PREFIX)
    assert "sk-secret" not in token
    assert secrets.decrypt_secret(token) == "sk-secret"
    assert secrets.encrypt_secret(token) == token


def test_invalid_key_falls_back(monkeypatch):
    monkeypatch.setenv("CABINET_ENCRYPTION_KEY", "not-a-key")
    assert secrets.encrypt_secret("abc") == "abc"


def test_wrong_key_returns_ciphertext(monkeypatch):
    monkeypatch.setenv("CABINET_ENCRYPTION_KEY", KEY)
    token = secrets.encrypt_secret("abc")
    assert token is not None

    monkeypatch.setenv("CABINET_ENCRYPTION_KEY", OTHER_KEY)
    assert secrets.decrypt_secret(token) == token
