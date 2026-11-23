"""Phase 5d Obsidian vault export."""

from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import (
    ContentFact,
    Document,
    DocumentCategory,
    File,
    Source,
)
from cabinet.db.repositories import CategoryRepository, ViewRepository
from cabinet.schemas import VaultExportRequest
from cabinet.services.vault_export import VaultExportService


async def _seed(session: AsyncSession, tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    original = source_dir / "invoice.pdf"
    original.write_bytes(b"invoice")

    source = Source(path=str(source_dir), label="Docs", recursive=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:inv", status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        File(
            source_id=source.id,
            document_id=document.id,
            content_hash=document.content_hash,
            path=str(original),
            filename="invoice.pdf",
            extension=".pdf",
            size_bytes=7,
            status="categorized",
        )
    )
    session.add(
        ContentFact(
            content_hash=document.content_hash,
            extractor_version="1",
            facts_version=2,
            summary="A consulting invoice.",
            doc_type="invoice",
            language="en",
            topics=["billing"],
        )
    )
    finance = await CategoryRepository.create(session, name="Finance")
    invoices = await CategoryRepository.create(
        session, name="Invoices", parent_id=finance.id
    )
    session.add(
        DocumentCategory(document_id=document.id, category_id=invoices.id)
    )
    await session.flush()
    return original


async def test_library_export_creates_catalogue_card(
    session: AsyncSession, tmp_path
):
    await _seed(session, tmp_path)
    target = tmp_path / "vault"
    request = VaultExportRequest(target_dir=str(target), scope="library")
    service = VaultExportService(session)

    plan = await service.plan(request)
    assert plan.notes == 1
    assert plan.creates == 1
    assert plan.conflicts == 0

    result = await service.apply(request)
    assert result.created == 1
    assert result.conflicts == 0

    note = target / "Cabinet" / "invoice.md"
    text = note.read_text()
    assert 'cabinet_hash: "sha256:inv"' in text
    assert 'doc_type: "invoice"' in text
    assert 'topics: ["billing"]' in text
    assert 'concepts: ["Finance > Invoices"]' in text
    assert 'tags: ["finance/invoices"]' in text
    assert "A consulting invoice." in text
    assert "[Open original](file://" in text
    assert 'source: "Docs"' in text

    again = await service.apply(request)
    assert again.created == 0
    assert again.conflicts == 1


async def test_existing_notes_are_never_overwritten(
    session: AsyncSession, tmp_path
):
    await _seed(session, tmp_path)
    target = tmp_path / "vault"
    folder = target / "Cabinet"
    folder.mkdir(parents=True)
    existing = folder / "invoice.md"
    existing.write_text("my notes")

    result = await VaultExportService(session).apply(
        VaultExportRequest(target_dir=str(target), scope="library")
    )

    assert result.created == 0
    assert result.conflicts == 1
    assert existing.read_text() == "my notes"


async def test_view_scope_exports_matching_documents(
    session: AsyncSession, tmp_path
):
    await _seed(session, tmp_path)
    view = await ViewRepository.create(
        session, name="Everything", query={}
    )
    target = tmp_path / "vault"
    request = VaultExportRequest(
        target_dir=str(target), scope="view", view_id=view.id
    )

    result = await VaultExportService(session).apply(request)

    assert result.created == 1
    assert (target / "Cabinet" / "invoice.md").exists()
