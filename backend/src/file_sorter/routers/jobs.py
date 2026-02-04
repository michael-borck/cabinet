"""Job monitoring and SSE endpoints."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from file_sorter.db.engine import async_session_factory, get_session
from file_sorter.db.repositories import JobRepository
from file_sorter.schemas import JobEvent, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

_TERMINAL_STATUSES = {"completed", "completed_with_errors", "cancelled", "failed"}


@router.get("", response_model=list[JobResponse])
async def list_jobs(session: AsyncSession = Depends(get_session)):
    """Return recent jobs, most recent first."""
    jobs = await JobRepository.list_recent(session)
    return jobs


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Return details for a single job."""
    job = await JobRepository.get_by_id(session, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return job


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Cancel a running job."""
    job = await JobRepository.cancel(session, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return job


@router.get("/{job_id}/events")
async def job_events(job_id: int):
    """Stream real-time job progress via Server-Sent Events.

    Polls the database every second and yields ``JobEvent`` payloads until
    the job reaches a terminal status (completed, cancelled, or failed).
    """

    async def event_generator():
        while True:
            async with async_session_factory() as session:
                job = await JobRepository.get_by_id(session, job_id)

            if job is None:
                # Yield an error event and stop.
                yield {
                    "event": "error",
                    "data": json.dumps({"message": f"Job {job_id} not found"}),
                }
                return

            event = JobEvent(
                job_id=job.id,
                status=job.status,
                processed_files=job.processed_files,
                total_files=job.total_files,
                message=None,
            )

            yield {
                "event": "progress",
                "data": event.model_dump_json(),
            }

            if job.status in _TERMINAL_STATUSES:
                return

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())
