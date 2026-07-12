"""Tests for pipeline status handling."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import File, Job, Source
from cabinet.services.pipeline import PipelineService


async def _setup(session: AsyncSession, tmp_path) -> tuple[Source, Job, File]:
    target = tmp_path / "doc.txt"
    target.write_text("some content")

    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()

    file = File(
        source_id=source.id,
        path=str(target),
        filename="doc.txt",
        extension=".txt",
        size_bytes=12,
        status="pending",
    )
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add_all([file, job])
    await session.commit()
    return source, job, file


async def test_no_llm_config_leaves_files_extracted(session: AsyncSession, tmp_path):
    """Without an LLM config, files must NOT be marked categorized."""
    _, job, file = await _setup(session, tmp_path)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(file)
    await session.refresh(job)
    assert file.status == "extracted"
    assert job.status == "completed"


async def test_extracted_files_are_retried(session: AsyncSession, tmp_path):
    """Files stranded in 'extracted' are picked up by the next run."""
    _, job, file = await _setup(session, tmp_path)
    file.status = "extracted"
    await session.commit()

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(job)
    assert job.total_files == 1  # the extracted file was selected


async def test_cancelled_job_is_not_resurrected(session: AsyncSession, tmp_path):
    """A cancelled job must stay cancelled, not flip to 'completed'."""
    _, job, _ = await _setup(session, tmp_path)
    job.status = "cancelled"
    await session.commit()

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(job)
    assert job.status == "cancelled"


async def test_extraction_error_recorded(session: AsyncSession, tmp_path):
    """An unreadable file is marked 'error' and counted on the job."""
    _, job, file = await _setup(session, tmp_path)
    file.path = str(tmp_path / "does-not-exist.txt")
    await session.commit()

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(file)
    await session.refresh(job)
    assert file.status == "error"
    assert job.error_count == 1
    assert job.status == "completed_with_errors"
    assert "extract" in (job.error_log or "")


async def test_progress_is_committed(session: AsyncSession, tmp_path, engine):
    """Job progress must be visible to other sessions during/after the run."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _, job, _ = await _setup(session, tmp_path)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    other_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with other_factory() as other:
        seen = (
            await other.execute(select(Job).where(Job.id == job.id))
        ).scalar_one()
        assert seen.status == "completed"
        assert seen.processed_files == 1


async def test_failed_batch_does_not_abort_run(
    session: AsyncSession, tmp_path, monkeypatch
):
    """A categorization failure in one batch must not kill later batches.

    Regression test: the rollback in the failure handler expires every File
    instance in the session, which used to crash the next batch's extraction.
    """
    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()

    files = []
    for name in ("a.txt", "b.txt"):
        target = tmp_path / name
        target.write_text("content of " + name)
        files.append(
            File(
                source_id=source.id,
                path=str(target),
                filename=name,
                extension=".txt",
                size_bytes=12,
                status="pending",
            )
        )
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add_all([*files, job])
    await session.commit()

    calls: list[list[int]] = []

    async def flaky_categorize(self, batch, extracted_texts):
        calls.append([f.id for f in batch])
        if len(calls) == 1:
            raise RuntimeError("LLM exploded")
        return {f.id for f in batch}

    monkeypatch.setattr(PipelineService, "_categorize_batch", flaky_categorize)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id, batch_size=1)

    await session.refresh(job)
    assert len(calls) == 2  # the second batch still ran
    assert job.status == "completed_with_errors"
    assert job.error_count == 1
    assert "[categorize]" in (job.error_log or "")

    statuses = (
        await session.execute(select(File.status).order_by(File.id))
    ).scalars().all()
    # Failed batch keeps its extraction result and will retry next run;
    # the following batch categorized normally.
    assert statuses == ["extracted", "categorized"]
