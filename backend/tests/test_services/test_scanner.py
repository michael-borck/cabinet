"""Tests for scanner reconciliation behavior."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import File, Job, Source
from cabinet.services.scanner import ScannerService


async def _make_source(
    session: AsyncSession,
    path: str,
    recursive: bool = True,
    exclude_patterns: list[str] | None = None,
) -> Source:
    source = Source(path=path, recursive=recursive, exclude_patterns=exclude_patterns)
    session.add(source)
    await session.flush()
    return source


async def _scanned_filenames(session: AsyncSession) -> set[str]:
    result = await session.execute(select(File))
    return {f.filename for f in result.scalars().all()}


async def test_rescan_is_idempotent(session: AsyncSession, tmp_path):
    """A second scan of an unchanged directory must not re-queue anything.

    Regression test for the aware/naive datetime mismatch that made every
    file look modified on every sync.
    """
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")
    source = await _make_source(session, str(tmp_path))

    scanner = ScannerService(session)
    job1 = await scanner.scan_source(source.id)
    assert job1.status == "completed"
    assert job1.processed_files == 2  # two new files

    # Simulate the pipeline having processed them.
    result = await session.execute(select(File))
    for file in result.scalars().all():
        file.status = "categorized"
    await session.flush()

    job2 = await scanner.scan_source(source.id)
    assert job2.status == "completed"
    assert job2.processed_files == 0  # nothing new, missing, or changed

    result = await session.execute(select(File).where(File.status == "pending"))
    assert result.scalars().all() == []


async def test_changed_file_requeued(session: AsyncSession, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("v1")
    source = await _make_source(session, str(tmp_path))

    scanner = ScannerService(session)
    await scanner.scan_source(source.id)

    result = await session.execute(select(File))
    file = result.scalar_one()
    file.status = "categorized"
    await session.flush()

    import os
    import time

    target.write_text("v2 with different content")
    # Force a visibly different mtime even on coarse filesystems.
    future = time.time() + 10
    os.utime(target, (future, future))

    job = await scanner.scan_source(source.id)
    assert job.processed_files == 1

    await session.refresh(file)
    assert file.status == "pending"


async def test_default_exclusions_skipped(session: AsyncSession, tmp_path):
    """Development directories and junk files are never scanned."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "report.txt").write_text("keep")

    node_modules = tmp_path / "node_modules" / "pkg"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text("skip")

    pycache = tmp_path / "docs" / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.pyc").write_text("skip")

    (tmp_path / "Thumbs.db").write_text("skip")
    (tmp_path / "~$report.docx").write_text("skip")

    source = await _make_source(session, str(tmp_path))
    job = await ScannerService(session).scan_source(source.id)

    assert job.status == "completed"
    assert await _scanned_filenames(session) == {"report.txt"}


async def test_per_source_glob_excludes(session: AsyncSession, tmp_path):
    """Glob patterns match bare names and paths relative to the source root."""
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / "skip.log").write_text("x")

    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "wip.txt").write_text("x")

    old = tmp_path / "projects" / "old-2019"
    old.mkdir(parents=True)
    (old / "legacy.txt").write_text("x")
    current = tmp_path / "projects" / "current"
    current.mkdir()
    (current / "active.txt").write_text("x")

    source = await _make_source(
        session, str(tmp_path), exclude_patterns=["*.log", "drafts", "projects/old*"]
    )
    await ScannerService(session).scan_source(source.id)

    assert await _scanned_filenames(session) == {"keep.txt", "active.txt"}


async def test_non_recursive_scan_respects_excludes(session: AsyncSession, tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / "skip.log").write_text("x")
    (tmp_path / "Thumbs.db").write_text("x")

    source = await _make_source(
        session, str(tmp_path), recursive=False, exclude_patterns=["*.log"]
    )
    await ScannerService(session).scan_source(source.id)

    assert await _scanned_filenames(session) == {"keep.txt"}


async def test_newly_excluded_files_marked_missing(session: AsyncSession, tmp_path):
    """Adding a pattern and rescanning retires already-indexed matches."""
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / "notes.tmp").write_text("x")

    source = await _make_source(session, str(tmp_path))
    scanner = ScannerService(session)
    await scanner.scan_source(source.id)
    assert await _scanned_filenames(session) == {"keep.txt", "notes.tmp"}

    source.exclude_patterns = ["*.tmp"]
    await session.flush()
    await scanner.scan_source(source.id)

    result = await session.execute(select(File).where(File.filename == "notes.tmp"))
    assert result.scalar_one().status == "missing"


async def test_scan_reuses_caller_job(session: AsyncSession, tmp_path):
    """When the router passes its job, no second job row is created."""
    (tmp_path / "a.txt").write_text("hello")
    source = await _make_source(session, str(tmp_path))

    job = Job(job_type="quick_sync", source_id=source.id)
    session.add(job)
    await session.flush()

    scanner = ScannerService(session)
    returned = await scanner.scan_source(source.id, job_id=job.id)
    assert returned.id == job.id

    result = await session.execute(select(Job))
    assert len(result.scalars().all()) == 1
