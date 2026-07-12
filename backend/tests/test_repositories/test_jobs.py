"""Tests for JobRepository lifecycle helpers."""

from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Job
from cabinet.db.repositories import JobRepository


async def test_fail_stale_jobs_sweeps_non_terminal(session: AsyncSession):
    """Jobs left running/pending by a dead process are marked failed."""
    session.add_all(
        [
            Job(job_type="quick_sync", status="running"),
            Job(job_type="quick_sync", status="pending"),
            Job(job_type="quick_sync", status="completed"),
            Job(job_type="quick_sync", status="failed"),
        ]
    )
    await session.commit()

    swept = await JobRepository.fail_stale_jobs(session)
    await session.commit()

    assert swept == 2
    jobs = await JobRepository.list_recent(session)
    statuses = sorted(j.status for j in jobs)
    assert statuses == ["completed", "failed", "failed", "failed"]
    interrupted = [j for j in jobs if j.error_log]
    assert len(interrupted) == 2
    assert all("interrupted" in j.error_log for j in interrupted)


async def test_fail_stale_jobs_noop_when_clean(session: AsyncSession):
    session.add(Job(job_type="quick_sync", status="completed"))
    await session.commit()

    swept = await JobRepository.fail_stale_jobs(session)

    assert swept == 0
