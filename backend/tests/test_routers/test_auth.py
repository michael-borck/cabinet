"""Tests for the CABINET_API_TOKEN auth middleware."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cabinet.app import create_app
from cabinet.db.engine import get_session

TOKEN = "test-token-abc123"


@pytest_asyncio.fixture
async def token_client(engine, monkeypatch) -> AsyncGenerator[AsyncClient, None]:
    monkeypatch.setenv("CABINET_API_TOKEN", TOKEN)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_request_without_token_rejected(token_client: AsyncClient):
    response = await token_client.get("/api/v1/sources")
    assert response.status_code == 401


async def test_request_with_wrong_token_rejected(token_client: AsyncClient):
    response = await token_client.get(
        "/api/v1/sources", headers={"X-API-Token": "wrong"}
    )
    assert response.status_code == 401


async def test_request_with_token_accepted(token_client: AsyncClient):
    response = await token_client.get(
        "/api/v1/sources", headers={"X-API-Token": TOKEN}
    )
    assert response.status_code == 200


async def test_health_exempt_from_token(token_client: AsyncClient):
    response = await token_client.get("/api/v1/health")
    assert response.status_code == 200


async def test_no_token_configured_leaves_api_open(client: AsyncClient):
    response = await client.get("/api/v1/sources")
    assert response.status_code == 200
