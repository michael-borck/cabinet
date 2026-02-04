"""Source management endpoints."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from file_sorter.db.engine import async_session_factory, get_session
from file_sorter.db.repositories import JobRepository, SourceRepository
from file_sorter.schemas import JobResponse, ScanRequest, SourceCreate, SourceResponse
from file_sorter.services.pipeline import PipelineService
from file_sorter.services.scanner import ScannerService

router = APIRouter(prefix="/sources", tags=["sources"])


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
    )
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
    """Background task that runs the scanner followed by the pipeline.

    Opens its own session so the request handler can return immediately.
    """
    async with async_session_factory() as session:
        try:
            scanner = ScannerService(session)
            await scanner.scan_source(source_id, mode=mode)
            await session.commit()
        except Exception:
            await session.rollback()
            # Mark the job as failed so the client can observe the error.
            async with async_session_factory() as err_session:
                await JobRepository.update_progress(
                    err_session, job_id, status="failed"
                )
                await err_session.commit()
            return

    async with async_session_factory() as session:
        try:
            pipeline = PipelineService(session)
            await pipeline.process_pending_files(job_id)
            await session.commit()
        except Exception:
            await session.rollback()
            async with async_session_factory() as err_session:
                await JobRepository.update_progress(
                    err_session, job_id, status="failed"
                )
                await err_session.commit()


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

    job = await JobRepository.create(
        session,
        job_type=body.mode,
        source_id=source_id,
    )
    # Flush so the job id is available before the background task starts.
    await session.flush()

    background_tasks.add_task(_run_scan_and_pipeline, source_id, body.mode, job.id)

    return job
