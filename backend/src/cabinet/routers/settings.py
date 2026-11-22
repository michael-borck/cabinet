"""LLM configuration / settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.db.repositories import LLMConfigRepository
from cabinet.llm.factory import create_provider
from cabinet.schemas import (
    EmbeddingConfigCreate,
    LLMConfigCreate,
    LLMConfigResponse,
    LLMTestResult,
)

router = APIRouter(prefix="/settings", tags=["settings"])

# Anthropic has no embeddings API (D5).
_EMBEDDING_PROVIDERS = {"ollama", "openai", "gemini"}


@router.get("/llm", response_model=LLMConfigResponse | None)
async def get_llm_config(session: AsyncSession = Depends(get_session)):
    """Return the currently active LLM configuration, or ``null``."""
    config = await LLMConfigRepository.get_active(session)
    if config is None:
        return None
    return config


@router.put(
    "/llm",
    response_model=LLMConfigResponse,
    status_code=status.HTTP_200_OK,
)
async def set_llm_config(
    body: LLMConfigCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create or replace the active LLM configuration.

    If no ``api_key`` is supplied and the provider is unchanged, the
    previously stored key is kept -- clients never see the raw key, so
    they cannot round-trip it themselves.
    """
    api_key = body.api_key
    if not api_key:
        existing = await LLMConfigRepository.get_active(session)
        if existing is not None and existing.provider == body.provider:
            api_key = existing.api_key

    config = await LLMConfigRepository.save(
        session,
        provider=body.provider,
        model=body.model,
        api_key=api_key,
        base_url=body.base_url,
        is_active=True,
    )
    return config


@router.post("/llm/test", response_model=LLMTestResult)
async def test_llm_connection(
    body: LLMConfigCreate | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Test an LLM provider connection.

    With a request body, the supplied values are tested (falling back to the
    stored key when none is given and the provider matches the stored config).
    Without a body, the stored active configuration is tested.
    """
    stored = await LLMConfigRepository.get_active(session)

    if body is not None:
        provider_name = body.provider
        model = body.model
        base_url = body.base_url
        api_key = body.api_key
        if not api_key and stored is not None and stored.provider == body.provider:
            api_key = stored.api_key
    elif stored is not None:
        provider_name = stored.provider
        model = stored.model
        base_url = stored.base_url
        api_key = stored.api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No LLM configuration to test. Supply one in the request "
            "body or set one via PUT /settings/llm.",
        )

    try:
        provider = create_provider(
            provider=provider_name,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
        ok = await provider.test_connection()
    except Exception as exc:
        return LLMTestResult(
            success=False,
            message=f"Connection failed: {exc}",
        )

    if ok:
        return LLMTestResult(
            success=True,
            message=f"Successfully connected to {provider_name}/{model}.",
        )
    return LLMTestResult(
        success=False,
        message=f"Provider {provider_name}/{model} returned a negative test result.",
    )


# ---------------------------------------------------------------------------
# Embedding configuration (Phase 2b)
# ---------------------------------------------------------------------------


@router.get("/embeddings", response_model=LLMConfigResponse | None)
async def get_embedding_config(session: AsyncSession = Depends(get_session)):
    """Return the active embedding configuration, or ``null``."""
    return await LLMConfigRepository.get_active(session, kind="embedding")


@router.put(
    "/embeddings",
    response_model=LLMConfigResponse,
    status_code=status.HTTP_200_OK,
)
async def set_embedding_config(
    body: EmbeddingConfigCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create or replace the active embedding configuration.

    Anthropic is rejected: it has no embeddings API. A stored key is kept
    when the provider is unchanged and none is supplied.
    """
    if body.provider.lower().strip() not in _EMBEDDING_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Provider has no embeddings API. Choose one of: "
                + ", ".join(sorted(_EMBEDDING_PROVIDERS))
            ),
        )

    api_key = body.api_key
    if not api_key:
        existing = await LLMConfigRepository.get_active(session, kind="embedding")
        if existing is not None and existing.provider == body.provider:
            api_key = existing.api_key

    return await LLMConfigRepository.save(
        session,
        provider=body.provider,
        model=body.model,
        api_key=api_key,
        base_url=body.base_url,
        is_active=True,
        kind="embedding",
    )


@router.post("/embeddings/test", response_model=LLMTestResult)
async def test_embedding_connection(
    body: EmbeddingConfigCreate | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Test an embedding config by embedding a short string.

    Uses the embedding endpoint rather than ``test_connection`` (which is a
    chat call an embedding-only model cannot answer).
    """
    stored = await LLMConfigRepository.get_active(session, kind="embedding")

    if body is not None:
        provider_name = body.provider
        model = body.model
        base_url = body.base_url
        api_key = body.api_key
        if not api_key and stored is not None and stored.provider == body.provider:
            api_key = stored.api_key
    elif stored is not None:
        provider_name = stored.provider
        model = stored.model
        base_url = stored.base_url
        api_key = stored.api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No embedding configuration to test. Supply one in the "
            "request body or set one via PUT /settings/embeddings.",
        )

    if provider_name.lower().strip() not in _EMBEDDING_PROVIDERS:
        return LLMTestResult(
            success=False,
            message=f"Provider {provider_name!r} has no embeddings API.",
        )

    try:
        provider = create_provider(
            provider=provider_name,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
        vectors = await provider.embed(["connection test"])
    except Exception as exc:
        return LLMTestResult(success=False, message=f"Embedding failed: {exc}")

    if not vectors or not vectors[0]:
        return LLMTestResult(
            success=False,
            message=f"Provider {provider_name}/{model} returned no vector.",
        )
    return LLMTestResult(
        success=True,
        message=(
            f"Embedded successfully with {provider_name}/{model} "
            f"(dim={len(vectors[0])})."
        ),
    )
