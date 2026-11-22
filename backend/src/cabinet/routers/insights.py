"""Taxonomy insights endpoints (Phase 3d)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.schemas import TaxonomyInsights
from cabinet.services.insights import TaxonomyInsightsService

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/taxonomy", response_model=TaxonomyInsights)
async def taxonomy_insights(session: AsyncSession = Depends(get_session)):
    """Read-only taxonomy drift metrics."""
    return await TaxonomyInsightsService(session).insights()
