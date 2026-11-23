"""Projection/export endpoints (Phase 5b)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.schemas import (
    FinderTagsPlan,
    FinderTagsResult,
    SymlinkTreePlan,
    SymlinkTreeRequest,
    SymlinkTreeResult,
    VaultExportPlan,
    VaultExportRequest,
    VaultExportResult,
)
from cabinet.services.finder_tags import FinderTagsService
from cabinet.services.symlink_export import SymlinkExportService
from cabinet.services.vault_export import VaultExportService

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


@router.post("/finder-tags/plan", response_model=FinderTagsPlan)
async def plan_finder_tags(session: AsyncSession = Depends(get_session)):
    """Dry-run: show which files would gain concept tags in Finder."""
    return await FinderTagsService(session).plan()


@router.post("/finder-tags/apply", response_model=FinderTagsResult)
async def apply_finder_tags(session: AsyncSession = Depends(get_session)):
    """Add concept names as native Finder tags (macOS, additive)."""
    return await FinderTagsService(session).apply()


@router.post("/obsidian/plan", response_model=VaultExportPlan)
async def plan_obsidian_export(
    body: VaultExportRequest,
    session: AsyncSession = Depends(get_session),
):
    """Dry-run: show the markdown notes an Obsidian export would create."""
    try:
        return await VaultExportService(session).plan(body)
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.post("/obsidian/apply", response_model=VaultExportResult)
async def apply_obsidian_export(
    body: VaultExportRequest,
    session: AsyncSession = Depends(get_session),
):
    """Write create-only markdown stubs under ``<target>/Cabinet``."""
    try:
        return await VaultExportService(session).apply(body)
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
