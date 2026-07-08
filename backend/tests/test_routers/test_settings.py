from httpx import AsyncClient


async def test_get_llm_config_empty(client: AsyncClient):
    response = await client.get("/api/v1/settings/llm")
    assert response.status_code == 200
    # Should be null/None when no config exists
    assert response.json() is None


async def test_set_llm_config(client: AsyncClient):
    response = await client.put("/api/v1/settings/llm", json={
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "test-key-123",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "openai"
    assert data["model"] == "gpt-4o-mini"
    assert data["is_active"] is True
    assert data["api_key_set"] is True


async def test_get_llm_config_after_set(client: AsyncClient):
    # Set first
    await client.put("/api/v1/settings/llm", json={
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
    })

    response = await client.get("/api/v1/settings/llm")
    assert response.status_code == 200
    data = response.json()
    assert data is not None
    assert data["provider"] == "anthropic"


async def test_api_key_never_serialized(client: AsyncClient):
    response = await client.put("/api/v1/settings/llm", json={
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "super-secret",
    })
    assert response.status_code == 200
    assert "api_key" not in response.json()

    response = await client.get("/api/v1/settings/llm")
    data = response.json()
    assert "api_key" not in data
    assert data["api_key_set"] is True


async def test_api_key_preserved_when_omitted(client: AsyncClient):
    await client.put("/api/v1/settings/llm", json={
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "super-secret",
    })

    # Re-save without a key (e.g. user changed only the model): key is kept.
    response = await client.put("/api/v1/settings/llm", json={
        "provider": "openai",
        "model": "gpt-4o",
    })
    assert response.json()["api_key_set"] is True

    # Switching provider without a key must NOT inherit the old key.
    response = await client.put("/api/v1/settings/llm", json={
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
    })
    assert response.json()["api_key_set"] is False
