"""Phase 5b symlink-tree export."""

import errno
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, DocumentCategory, File, Source
from cabinet.db.repositories import CategoryRepository, ViewRepository
from cabinet.schemas import SymlinkTreeRequest
from cabinet.services.symlink_export import SymlinkExportService


async def _seed(session: AsyncSession, tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    invoice_path = source_dir / "invoice.pdf"
    invoice_path.write_bytes(b"invoice")
    notes_path = source_dir / "notes.pdf"
    notes_path.write_bytes(b"notes")

    source = Source(path=str(source_dir), recursive=True)
    session.add(source)
    await session.flush()

    invoice_doc = Document(content_hash="sha256:inv", status="categorized")
    notes_doc = Document(content_hash="sha256:notes", status="categorized")
    session.add_all([invoice_doc, notes_doc])
    await session.flush()
    session.add_all(
        [
            File(
                source_id=source.id,
                document_id=invoice_doc.id,
                content_hash=invoice_doc.content_hash,
                path=str(invoice_path),
                filename="invoice.pdf",
                extension=".pdf",
                size_bytes=7,
                status="categorized",
            ),
            File(
                source_id=source.id,
                document_id=notes_doc.id,
                content_hash=notes_doc.content_hash,
                path=str(notes_path),
                filename="notes.pdf",
                extension=".pdf",
                size_bytes=5,
                status="categorized",
            ),
        ]
    )
    finance = await CategoryRepository.create(session, name="Finance")
    invoices = await CategoryRepository.create(
        session, name="Invoices", parent_id=finance.id
    )
    session.add(
        DocumentCategory(document_id=invoice_doc.id, category_id=invoices.id)
    )
    await session.flush()
    return source_dir, invoice_path, notes_path, invoice_doc


async def test_library_plan_apply_refresh_and_removal(
    session: AsyncSession, tmp_path
):
    _source_dir, invoice_path, notes_path, invoice_doc = await _seed(
        session, tmp_path
    )
    target = tmp_path / "export"
    service = SymlinkExportService(session)
    request = SymlinkTreeRequest(target_dir=str(target), scope="library")

    plan = await service.plan(request)
    assert plan.supported is True
    assert plan.creates == 2
    assert plan.conflicts == 0

    result = await service.apply(request)
    assert result.created == 2
    assert result.errors == []

    invoice_link = target / "finance" / "invoices" / "invoice.pdf"
    notes_link = target / "_uncategorised" / "notes.pdf"
    assert invoice_link.is_symlink()
    assert os.path.realpath(invoice_link) == os.path.realpath(invoice_path)
    assert notes_link.is_symlink()
    assert os.path.realpath(notes_link) == os.path.realpath(notes_path)
    assert (target / ".cabinet-export.json").exists()

    again = await service.plan(request)
    assert again.keeps == 2
    assert again.creates == 0

    # A non-generated file in the target is never touched.
    keep = target / "keep.txt"
    keep.write_text("mine")
    await service.apply(request)
    assert keep.read_text() == "mine"

    # Removing the assignment moves the document to _uncategorised.
    assignment = (
        await session.execute(
            select(DocumentCategory)
        )
    ).scalars().first()
    await session.delete(assignment)
    await session.flush()

    moved = await service.plan(request)
    assert moved.removes == 1
    assert moved.creates == 1

    await service.apply(request)
    assert not invoice_link.is_symlink()
    moved_link = target / "_uncategorised" / "invoice.pdf"
    assert moved_link.is_symlink()
    assert os.path.realpath(moved_link) == os.path.realpath(invoice_path)
    assert keep.read_text() == "mine"


async def test_view_export(session: AsyncSession, tmp_path):
    await _seed(session, tmp_path)
    view = await ViewRepository.create(
        session,
        name="Everything",
        query={},
    )
    target = tmp_path / "view-export"
    request = SymlinkTreeRequest(
        target_dir=str(target), scope="view", view_id=view.id
    )

    plan = await SymlinkExportService(session).plan(request)
    assert plan.supported is True
    assert plan.creates == 2  # both documents match the (empty) query

    await SymlinkExportService(session).apply(request)
    assert (target / "everything" / "invoice.pdf").is_symlink()


async def test_unsupported_filesystem_is_reported(
    session: AsyncSession, tmp_path, monkeypatch
):
    await _seed(session, tmp_path)

    def fail_symlink(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(
        "cabinet.services.symlink_export.os.symlink", fail_symlink
    )
    plan = await SymlinkExportService(session).plan(
        SymlinkTreeRequest(
            target_dir=str(tmp_path / "unsupported"), scope="library"
        )
    )

    assert plan.supported is False
    assert "Symlinks" in (plan.message or "")
