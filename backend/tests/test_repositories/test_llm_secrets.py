"""LLM API keys are encrypted at rest when a key is configured."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.repositories import LLMConfigRepository
from cabinet.utils import secrets

KEY = "11" * 32


async def test_api_key_is_encrypted_in_the_database(
    session: AsyncSession, monkeypatch
):
    monkeypatch.setenv("CABINET_ENCRYPTION_KEY", KEY)

    config = await LLMConfigRepository.save(
        session,
        provider="openai",
        model="gpt-test",
        api_key="sk-secret",
        is_active=True,
    )
    await session.commit()

    raw = (
        await session.execute(
            text("SELECT api_key FROM llm_configs WHERE id = :id"),
            {"id": config.id},
        )
    ).scalar()
    assert raw.startswith(secrets.CIPHER_PREFIX)
    assert "sk-secret" not in raw

    session.expire_all()
    loaded = await LLMConfigRepository.get_active(session)
    assert loaded is not None
    assert loaded.api_key == "sk-secret"


async def test_legacy_plaintext_key_is_migrated(
    session: AsyncSession, monkeypatch
):
    await session.execute(
        text(
            "INSERT INTO llm_configs "
            "(provider, model, api_key, is_active, kind) "
            "VALUES ('openai', 'gpt-test', 'plain-key', 1, 'chat')"
        )
    )
    await session.commit()

    monkeypatch.setenv("CABINET_ENCRYPTION_KEY", KEY)
    migrated = await LLMConfigRepository.encrypt_legacy_keys(session)
    await session.commit()

    assert migrated == 1
    raw = (
        await session.execute(text("SELECT api_key FROM llm_configs"))
    ).scalar()
    assert raw.startswith(secrets.CIPHER_PREFIX)
    assert "plain-key" not in raw
