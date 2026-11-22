"""Tests for scanner reconciliation behavior."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import (
    Category,
    Document,
    DocumentCategory,
    File,
    Job,
    Source,
)
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


async def test_rename_preserves_identity_and_status(session: AsyncSession, tmp_path):
    """An external rename is a move, not a delete + new file (Phase 1a).

    The categorised file must keep its row (and therefore its category
    assignments) and must not be re-queued for extraction/LLM work.
    """
    target = tmp_path / "a.txt"
    target.write_text("hello")
    source = await _make_source(session, str(tmp_path))

    scanner = ScannerService(session)
    await scanner.scan_source(source.id)

    result = await session.execute(select(File))
    file = result.scalar_one()
    original_id = file.id
    file.status = "categorized"
    await session.flush()

    target.rename(tmp_path / "renamed.txt")
    job = await scanner.scan_source(source.id)

    result = await session.execute(select(File))
    files = result.scalars().all()
    assert len(files) == 1
    moved = files[0]
    assert moved.id == original_id
    assert moved.filename == "renamed.txt"
    assert moved.status == "categorized"
    assert job.processed_files == 1  # the move itself, nothing re-queued


async def _mark_categorized(session: AsyncSession, file: File) -> Document:
    """Simulate a completed pipeline run for scanner tests."""
    doc = await session.get(Document, file.document_id)
    assert doc is not None
    doc.status = "categorized"
    file.status = "categorized"
    await session.flush()
    return doc


async def test_move_detected_by_hash_without_inode(session: AsyncSession, tmp_path):
    """FAT-style fallback: no usable inode, the hash still spots the move."""
    target = tmp_path / "a.txt"
    target.write_text("hello")
    source = await _make_source(session, str(tmp_path))
    scanner = ScannerService(session)
    await scanner.scan_source(source.id)

    file = (await session.execute(select(File))).scalar_one()
    await _mark_categorized(session, file)
    original_id = file.id
    file.inode = None  # simulate a filesystem without usable inodes
    file.device_id = None
    await session.flush()

    subdir = tmp_path / "sub"
    subdir.mkdir()
    target.rename(subdir / "moved.txt")
    job = await scanner.scan_source(source.id)

    files = (await session.execute(select(File))).scalars().all()
    assert len(files) == 1
    assert files[0].id == original_id
    assert files[0].filename == "moved.txt"
    assert files[0].status == "categorized"
    assert job.moved_count == 1


async def test_copy_is_second_instance_of_same_document(
    session: AsyncSession, tmp_path
):
    """Identical content at a new path attaches to the same document."""
    (tmp_path / "a.txt").write_text("shared content")
    source = await _make_source(session, str(tmp_path))
    scanner = ScannerService(session)
    await scanner.scan_source(source.id)

    file = (await session.execute(select(File))).scalar_one()
    doc = await _mark_categorized(session, file)

    (tmp_path / "copy.txt").write_text("shared content")
    await scanner.scan_source(source.id)

    files = (await session.execute(select(File).order_by(File.filename))).scalars().all()
    assert [f.filename for f in files] == ["a.txt", "copy.txt"]
    assert {f.document_id for f in files} == {doc.id}
    # The copy needed no pipeline work: it sights a categorized document.
    assert {f.status for f in files} == {"categorized"}
    docs = (await session.execute(select(Document))).scalars().all()
    assert len(docs) == 1


async def test_content_replaced_at_path_repoints_document(
    session: AsyncSession, tmp_path
):
    """New content at an old path is a new document, re-queued once."""
    import os
    import time

    target = tmp_path / "a.txt"
    target.write_text("v1")
    source = await _make_source(session, str(tmp_path))
    scanner = ScannerService(session)
    await scanner.scan_source(source.id)

    file = (await session.execute(select(File))).scalar_one()
    old_doc = await _mark_categorized(session, file)

    target.write_text("completely different v2")
    future = time.time() + 10
    os.utime(target, (future, future))
    await scanner.scan_source(source.id)

    await session.refresh(file)
    assert file.document_id != old_doc.id
    assert file.status == "pending"
    new_doc = await session.get(Document, file.document_id)
    assert new_doc is not None and new_doc.status == "pending"
    assert new_doc.content_hash is not None
    assert new_doc.content_hash != old_doc.content_hash


async def test_provisional_documents_merge_on_hash_backfill(
    session: AsyncSession, tmp_path
):
    """Pre-1a rows (provisional documents) merge when hashing catches up."""
    (tmp_path / "a.txt").write_text("same bytes")
    (tmp_path / "b.txt").write_text("same bytes")
    source = await _make_source(session, str(tmp_path))

    docs = [Document(content_hash=None, status="pending") for _ in range(2)]
    session.add_all(docs)
    await session.flush()
    files = [
        File(
            source_id=source.id,
            document_id=doc.id,
            path=str(tmp_path / name),
            filename=name,
            extension=".txt",
            size_bytes=10,
            status="pending",
        )
        for doc, name in zip(docs, ("a.txt", "b.txt"), strict=True)
    ]
    category = Category(name="Finance", slug="finance")
    session.add_all([*files, category])
    await session.flush()
    session.add(
        DocumentCategory(
            document_id=docs[1].id, category_id=category.id, is_manual=True
        )
    )
    await session.flush()

    await ScannerService(session).scan_source(source.id)

    remaining = (await session.execute(select(Document))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].content_hash is not None
    merged_files = (await session.execute(select(File))).scalars().all()
    assert {f.document_id for f in merged_files} == {remaining[0].id}
    # The manual assignment survived the merge at document level.
    assignments = (
        await session.execute(select(DocumentCategory))
    ).scalars().all()
    assert [(a.category_id, a.is_manual) for a in assignments] == [(category.id, True)]


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
