"""Portable curation sidecar endpoints (Phase 5a)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.services.curation import export_data, import_data

router = APIRouter(prefix="/curation", tags=["curation"])


@router.get("/export")
async def export_curation(session: AsyncSession = Depends(get_session)):
    """Return the portable curation sidecar document."""
    return await export_data(session)


@router.post("/import")
async def import_curation(
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """Apply a curation sidecar additively (idempotent)."""
    try:
        return await import_data(session, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
