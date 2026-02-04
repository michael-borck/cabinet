"""LLM configuration / settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from file_sorter.db.engine import get_session
from file_sorter.db.repositories import LLMConfigRepository
from file_sorter.llm.factory import create_provider
from file_sorter.schemas import LLMConfigCreate, LLMConfigResponse, LLMTestResult

router = APIRouter(prefix="/settings", tags=["settings"])


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
    """Create or replace the active LLM configuration."""
    config = await LLMConfigRepository.save(
        session,
        provider=body.provider,
        model=body.model,
        api_key=body.api_key,
        base_url=body.base_url,
        is_active=True,
    )
    return config


@router.post("/llm/test", response_model=LLMTestResult)
async def test_llm_connection(
    session: AsyncSession = Depends(get_session),
):
    """Test the active LLM provider connection.

    Returns success/failure with a descriptive message.
    """
    config = await LLMConfigRepository.get_active(session)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active LLM configuration found. Set one first via PUT /settings/llm.",
        )

    try:
        provider = create_provider(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
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
            message=f"Successfully connected to {config.provider}/{config.model}.",
        )
    return LLMTestResult(
        success=False,
        message=f"Provider {config.provider}/{config.model} returned a negative test result.",
    )
