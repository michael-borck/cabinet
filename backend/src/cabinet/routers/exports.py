"""Projection/export endpoints (Phase 5b)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.schemas import SymlinkTreePlan, SymlinkTreeRequest, SymlinkTreeResult
from cabinet.services.symlink_export import SymlinkExportService

router = APIRouter(prefix="/exports", tags=["exports"])


@router.post("/symlink-tree/plan", response_model=SymlinkTreePlan)
async def plan_symlink_tree(
    body: SymlinkTreeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Dry-run: show what a symlink-tree export would create/update/remove."""
    try:
        return await SymlinkExportService(session).plan(body)
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.post("/symlink-tree/apply", response_model=SymlinkTreeResult)
async def apply_symlink_tree(
    body: SymlinkTreeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Materialize the symlink tree, tracking generated links in a manifest."""
    try:
        return await SymlinkExportService(session).apply(body)
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
