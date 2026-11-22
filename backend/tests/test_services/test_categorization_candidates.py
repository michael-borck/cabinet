"""Phase 3b candidate-constrained assignment and promotion."""

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import (
    Category,
    Document,
    DocumentCategory,
    File,
    Source,
)
from cabinet.db.repositories import CategoryRepository
from cabinet.services.categorization import CategorizationService
from cabinet.utils.embeddings import pack_embedding


class CapturingProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[tuple[str, str]] = []

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.prompts.append((system_prompt, user_prompt))
        return self.payload


async def _file(
    session: AsyncSession, tmp_path, name="doc.pdf", digest="sha256:doc1"
) -> tuple[File, Document]:
    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash=digest, status="pending")
    session.add(document)
    await session.flush()
    file = File(
        source_id=source.id,
        document_id=document.id,
        content_hash=digest,
        path=str(tmp_path / name),
        filename=name,
        extension=".pdf",
        size_bytes=1,
        status="pending",
    )
    session.add(file)
    await session.flush()
    return file, document


async def test_candidate_ids_drive_assignment(session: AsyncSession, tmp_path):
    invoice = await CategoryRepository.create(session, name="Finance > Invoices")
    await CategoryRepository.set_embedding(
        session,
        invoice.id,
        embedding=pack_embedding([1.0, 0.0]),
        model="m",
        dim=2,
    )
    travel = await CategoryRepository.create(session, name="Travel")
    await CategoryRepository.set_embedding(
        session,
        travel.id,
        embedding=pack_embedding([0.0, 1.0]),
        model="m",
        dim=2,
    )
    file, _ = await _file(session, tmp_path)

    fact = SimpleNamespace(
        embedding=pack_embedding([1.0, 0.0]),
        summary="An invoice for consulting",
        doc_type="invoice",
        topics=["billing"],
        entities={},
    )
    provider = CapturingProvider(
        {
            "files": [
                {
                    "file_index": 0,
                    "assignments": [
                        {"concept_id": invoice.id, "confidence": 0.93}
                    ],
                }
            ]
        }
    )

    await CategorizationService(session, provider).categorize_files(
        [file], facts={file.id: fact}
    )

    prompt = provider.prompts[0][1]
    assert "Candidate concepts" in prompt
    assert f"id={invoice.id}" in prompt
    assert f"id={travel.id}" in prompt

    rows = (
        await session.execute(
            select(DocumentCategory).where(
                DocumentCategory.document_id == file.document_id
            )
        )
    ).scalars().all()
    assert {row.category_id for row in rows} == {invoice.id}


async def test_new_name_fallback_creates_provisional(session: AsyncSession, tmp_path):
    file, _ = await _file(session, tmp_path, name="zebra.pdf", digest="sha256:z")
    fact = SimpleNamespace(
        embedding=None,
        summary="Notes about zebras",
        doc_type="note",
        topics=[],
        entities={},
    )
    provider = CapturingProvider(
        {
            "files": [
                {
                    "file_index": 0,
                    "assignments": [
                        {"new_name": "Animals > Zebras", "confidence": 0.8}
                    ],
                }
            ]
        }
    )

    await CategorizationService(session, provider).categorize_files(
        [file], facts={file.id: fact}
    )

    # The tree prompt is used when there are no candidates.
    assert "Existing categories" in provider.prompts[0][1]
    created = (
        await session.execute(select(Category).where(Category.name == "Zebras"))
    ).scalar_one()
    assert created.status == "provisional"
    assert created.origin == "ai"


async def test_provisional_concept_promotes_after_threshold(
    session: AsyncSession, tmp_path
):
    ready = await CategoryRepository.create(
        session, name="Drafts", status="provisional", origin="ai"
    )
    slow = await CategoryRepository.create(
        session, name="Ideas", status="provisional", origin="ai"
    )
    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()

    for index in range(3):
        document = Document(content_hash=f"sha256:p{index}", status="categorized")
        session.add(document)
        await session.flush()
        session.add(
            DocumentCategory(document_id=document.id, category_id=ready.id)
        )
        if index < 2:
            session.add(
                DocumentCategory(document_id=document.id, category_id=slow.id)
            )
    await session.flush()

    assert (
        await CategoryRepository.promote_if_ready(session, ready.id, threshold=3)
        is True
    )
    assert ready.status == "approved"
    assert (
        await CategoryRepository.promote_if_ready(session, slow.id, threshold=3)
        is False
    )
    assert slow.status == "provisional"
