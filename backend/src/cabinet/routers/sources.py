"""Source management endpoints."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import async_session_factory, get_session
from cabinet.db.models import Job
from cabinet.db.repositories import (
    ContentFactRepository,
    JobRepository,
    SourceRepository,
)
from cabinet.schemas import (
    JobResponse,
    ScanRequest,
    SourceCreate,
    SourceResponse,
    SourceUpdate,
)
from cabinet.services.sync import run_scan_and_pipeline

router = APIRouter(prefix="/sources", tags=["sources"])

# Note: the folder watcher picks up create/update/delete changes by
# re-reading watched sources on its own short refresh tick, so these
# endpoints need no watcher coupling (and no commit-ordering concerns).


@router.get("", response_model=list[SourceResponse])
async def list_sources(session: AsyncSession = Depends(get_session)):
    """Return all registered sources."""
    sources = await SourceRepository.list_all(session)
    return sources


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreate,
    session: AsyncSession = Depends(get_session),
):
    """Register a new source directory.

    The path must exist on the filesystem.
    """
    resolved = str(Path(body.path).resolve())
    if not os.path.isdir(resolved):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist or is not a directory: {body.path}",
        )

    source = await SourceRepository.create(
        session,
        path=resolved,
        label=body.label,
        recursive=body.recursive,
        exclude_patterns=body.exclude_patterns,
        watch_enabled=body.watch_enabled,
        store_facts=body.store_facts,
    )
    return source


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: int,
    body: SourceUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update a source's label, recursive flag, or exclusion patterns.

    Only fields present in the request body are changed. Exclusions apply on
    the next scan; already-indexed files matching a new pattern are marked
    ``missing`` by that scan.
    """
    fields = body.model_dump(exclude_unset=True)
    source = await SourceRepository.update(session, source_id, **fields)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )
    # Tier 1 is shared per content hash: turning it off must only drop facts
    # no other facts-enabled source still references (design §5.4).
    if fields.get("store_facts") is False:
        await ContentFactRepository.purge_unreferenced(session)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Delete a source and cascade-delete its files."""
    deleted = await SourceRepository.delete(session, source_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )


async def _run_scan_and_pipeline(source_id: int, mode: str, job_id: int) -> None:
    """Background task: shared scanner+pipeline runner (services/sync)."""
    await run_scan_and_pipeline(async_session_factory, source_id, mode, job_id)


@router.post(
    "/{source_id}/scan",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def scan_source(
    source_id: int,
    body: ScanRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Trigger a filesystem scan for a source.

    Returns the created job immediately; scanning runs in the background.
    """
    source = await SourceRepository.get_by_id(session, source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )

    # One scan per source at a time: concurrent pipelines would select the
    # same pending files and categorize them twice.
    active = await session.execute(
        select(Job.id)
        .where(Job.source_id == source_id, Job.status.in_(("pending", "running")))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scan is already running for this source",
        )

    job = await JobRepository.create(
        session,
        job_type=body.mode,
        source_id=source_id,
    )
    # Flush so the job id is available before the background task starts.
    await session.flush()

    background_tasks.add_task(_run_scan_and_pipeline, source_id, body.mode, job.id)

    return job
