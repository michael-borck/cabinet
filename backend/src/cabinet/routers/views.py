"""Saved live views (Phase 4a, design §5)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.db.repositories import SourceRepository, ViewRepository
from cabinet.routers.files import _build_file_response
from cabinet.schemas import (
    FileListResponse,
    ViewCreate,
    ViewFacets,
    ViewGroups,
    ViewPreviewRequest,
    ViewQuery,
    ViewResponse,
    ViewUpdate,
)
from cabinet.services.views import ViewPage, ViewQueryService

router = APIRouter(prefix="/views", tags=["views"])


@router.get("", response_model=list[ViewResponse])
async def list_views(session: AsyncSession = Depends(get_session)):
    """Return saved views, pinned first."""
    views = await ViewRepository.list_all(session)
    return [ViewResponse.model_validate(view) for view in views]


@router.post("", response_model=ViewResponse, status_code=status.HTTP_201_CREATED)
async def create_view(
    body: ViewCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a saved view from a query spec."""
    view = await ViewRepository.create(
        session,
        name=body.name,
        description=body.description,
        icon=body.icon,
        color=body.color,
        query=body.query.model_dump(),
        pinned=body.pinned,
    )
    return ViewResponse.model_validate(view)


@router.get("/{view_id}", response_model=ViewResponse)
async def get_view(
    view_id: int,
    session: AsyncSession = Depends(get_session),
):
    view = await ViewRepository.get(session, view_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"View {view_id} not found",
        )
    return ViewResponse.model_validate(view)


@router.patch("/{view_id}", response_model=ViewResponse)
async def update_view(
    view_id: int,
    body: ViewUpdate,
    session: AsyncSession = Depends(get_session),
):
    fields = body.model_dump(exclude_unset=True)
    if "query" in fields:
        if fields["query"] is None:
            fields.pop("query")
        else:
            fields["query"] = ViewQuery.model_validate(fields["query"]).model_dump()

    view = await ViewRepository.update(session, view_id, **fields)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"View {view_id} not found",
        )
    return ViewResponse.model_validate(view)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_view(
    view_id: int,
    session: AsyncSession = Depends(get_session),
):
    if not await ViewRepository.delete(session, view_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"View {view_id} not found",
        )


@router.get("/{view_id}/files", response_model=FileListResponse)
async def view_files(
    view_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """Evaluate a saved view, collapsing version clusters by default."""
    view = await ViewRepository.get(session, view_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"View {view_id} not found",
        )
    query = ViewQuery.model_validate(view.query)
    result = await ViewQueryService(session).page(
        query, offset=(page - 1) * per_page, limit=per_page
    )
    return await _file_list(session, result, page=page, per_page=per_page)


@router.get("/{view_id}/facets", response_model=ViewFacets)
async def view_facets(
    view_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Cluster-aware facet counts for a saved view (Phase 4b)."""
    view = await ViewRepository.get(session, view_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"View {view_id} not found",
        )
    query = ViewQuery.model_validate(view.query)
    return await ViewQueryService(session).facets(query)


@router.get("/{view_id}/groups", response_model=ViewGroups)
async def view_groups(
    view_id: int,
    group_by: Literal[
        "none", "concept", "doc_type", "topic", "source", "month"
    ]
    | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Grouped counts for a saved view, optionally overriding its dimension."""
    view = await ViewRepository.get(session, view_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"View {view_id} not found",
        )
    query = ViewQuery.model_validate(view.query)
    return await ViewQueryService(session).groups(query, group_by=group_by)


@router.post("/preview", response_model=FileListResponse)
async def preview_view(
    body: ViewPreviewRequest,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """Evaluate an unsaved query for the builder's live feedback."""
    result = await ViewQueryService(session).page(
        body.query, offset=(page - 1) * per_page, limit=per_page
    )
    return await _file_list(session, result, page=page, per_page=per_page)


async def _file_list(
    session: AsyncSession, result: ViewPage, *, page: int, per_page: int
) -> FileListResponse:
    sources = await SourceRepository.list_all(session)
    available = {
        source.id: source.availability == "available" for source in sources
    }
    responses = []
    for file in result.files:
        response = _build_file_response(
            file, source_available=available.get(file.source_id, True)
        )
        response.version_count = result.version_counts.get(file.id, 1)
        responses.append(response)
    return FileListResponse(
        files=responses,
        total=result.total,
        page=page,
        per_page=per_page,
    )
