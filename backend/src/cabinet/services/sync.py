"""Shared scan+pipeline orchestration.

Used by the manual scan endpoint and by the folder watcher (Phase 1b), so
both paths create/track jobs and handle failures identically.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cabinet.db.models import Job
from cabinet.db.repositories import JobRepository
from cabinet.services.pipeline import PipelineService
from cabinet.services.scanner import ScannerService

logger = logging.getLogger(__name__)


async def run_scan_and_pipeline(
    session_factory: async_sessionmaker,
    source_id: int,
    mode: str,
    job_id: int,
) -> None:
    """Run the scanner then the pipeline against an existing job row.

    Each stage opens its own session; a stage failure marks the job failed
    (so clients can observe it) instead of raising into the caller.
    """
    async with session_factory() as session:
        try:
            scanner = ScannerService(session)
            await scanner.scan_source(source_id, mode=mode, job_id=job_id)
            await session.commit()
        except Exception:
            logger.exception("Scan failed for source %d (job %d)", source_id, job_id)
            await session.rollback()
            await _mark_failed(session_factory, job_id)
            return

    async with session_factory() as session:
        try:
            pipeline = PipelineService(session)
            await pipeline.process_pending_files(job_id)
            await session.commit()
        except Exception:
            logger.exception(
                "Pipeline failed for source %d (job %d)", source_id, job_id
            )
            await session.rollback()
            await _mark_failed(session_factory, job_id)


async def create_and_run_sync(
    session_factory: async_sessionmaker,
    source_id: int,
    *,
    mode: str = "quick_sync",
    trigger: str = "manual",
) -> int | None:
    """Create a job for *source_id* and run it to completion.

    Returns the job id, or None when a scan for this source is already
    active (single-flight across manual and watcher-triggered runs: two
    concurrent pipelines would select the same pending documents).
    """
    async with session_factory() as session:
        active = await session.execute(
            select(Job.id)
            .where(
                Job.source_id == source_id,
                Job.status.in_(("pending", "running")),
            )
            .limit(1)
        )
        if active.scalar_one_or_none() is not None:
            logger.debug("Sync already active for source %d; skipping", source_id)
            return None
        job = await JobRepository.create(
            session, job_type=mode, source_id=source_id, trigger=trigger
        )
        await session.commit()
        job_id = job.id

    await run_scan_and_pipeline(session_factory, source_id, mode, job_id)
    return job_id


async def _mark_failed(session_factory: async_sessionmaker, job_id: int) -> None:
    async with session_factory() as err_session:
        await JobRepository.update_progress(err_session, job_id, status="failed")
        await err_session.commit()
