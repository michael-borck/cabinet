"""Phase 3c merge and consolidation proposals."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import (
    Document,
    DocumentCategory,
    File,
    FileCategory,
    Source,
)
from cabinet.db.repositories import CategoryRepository
from cabinet.services.consolidation import ConsolidationService
from cabinet.utils.embeddings import pack_embedding


class FakeProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.prompts.append(user_prompt)
        return self.payload


async def _assign_document(
    session: AsyncSession, category_id: int, digest: str, *, confidence: float,
    manual: bool = False,
) -> Document:
    document = Document(content_hash=digest, status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        DocumentCategory(
            document_id=document.id,
            category_id=category_id,
            confidence=confidence,
            is_manual=manual,
        )
    )
    return document


async def test_merge_repoints_assignments_aliases_and_examples(
    session: AsyncSession, tmp_path
):
    source = await CategoryRepository.create(session, name="Billing")
    target = await CategoryRepository.create(session, name="Invoices")
    await CategoryRepository.add_alias(session, source.id, "Bills")
    await CategoryRepository.add_example(
        session, source.id, filename="bill.pdf", content_hash="sha256:x",
        summary_snippet="a bill", source="manual",
    )

    both = Document(content_hash="sha256:both", status="categorized")
    session.add(both)
    await session.flush()
    session.add(
        DocumentCategory(
            document_id=both.id,
            category_id=source.id,
            confidence=0.4,
            is_manual=True,
        )
    )
    session.add(
        DocumentCategory(
            document_id=both.id, category_id=target.id, confidence=0.9
        )
    )
    only = Document(content_hash="sha256:only", status="categorized")
    session.add(only)
    await session.flush()
    session.add(
        DocumentCategory(
            document_id=only.id, category_id=source.id, confidence=0.7
        )
    )

    real_source = Source(path=str(tmp_path), recursive=True)
    session.add(real_source)
    await session.flush()
    source_file = File(
        source_id=real_source.id,
        path=str(tmp_path / "bill.pdf"),
        filename="bill.pdf",
        size_bytes=1,
        status="categorized",
    )
    session.add(source_file)
    await session.flush()
    session.add(
        FileCategory(file_id=source_file.id, category_id=source.id, confidence=0.5)
    )
    await session.flush()

    result = await CategoryRepository.merge(session, source.id, target.id)
    assert result is not None
    assert result.source.status == "merged"
    assert result.source.merged_into_id == target.id
    assert result.assignments_moved == 3

    both_rows = (
        await session.execute(
            select(DocumentCategory).where(
                DocumentCategory.document_id == both.id
            )
        )
    ).scalars().all()
    assert len(both_rows) == 1
    assert both_rows[0].category_id == target.id
    assert both_rows[0].is_manual is True
    assert both_rows[0].confidence == 0.9

    instance_rows = (
        await session.execute(
            select(FileCategory).where(FileCategory.file_id == source_file.id)
        )
    ).scalars().all()
    assert len(instance_rows) == 1
    assert instance_rows[0].category_id == target.id

    await session.refresh(target, attribute_names=["aliases", "examples"])
    assert {"Billing", "Bills"} <= {alias.alias for alias in target.aliases}
    assert {example.filename for example in target.examples} == {"bill.pdf"}
    assert await CategoryRepository.get_document_count(session, source.id) == 0


async def test_merge_rejects_self_and_merged_source(session: AsyncSession):
    category = await CategoryRepository.create(session, name="Solo")
    with pytest.raises(ValueError):
        await CategoryRepository.merge(session, category.id, category.id)

    target = await CategoryRepository.create(session, name="Target")
    await CategoryRepository.merge(session, category.id, target.id)
    with pytest.raises(ValueError):
        await CategoryRepository.merge(session, category.id, target.id)


async def test_rename_updates_slug_and_invalidates_embedding(session: AsyncSession):
    category = await CategoryRepository.create(session, name="Old Name")
    await CategoryRepository.set_embedding(
        session, category.id, embedding=b"vector", model="m", dim=1
    )

    updated = await CategoryRepository.update(
        session, category.id, name="New Name"
    )

    assert updated is not None
    assert updated.name == "New Name"
    assert updated.slug == "new-name"
    assert updated.embedding is None
    assert updated.embedding_model is None


async def test_deterministic_merges_rank_similar_concepts(
    session: AsyncSession,
):
    invoice = await CategoryRepository.create(session, name="Invoice")
    bills = await CategoryRepository.create(session, name="Bills")
    await CategoryRepository.set_embedding(
        session, invoice.id, embedding=pack_embedding([1.0, 0.0]), model="m", dim=2
    )
    await CategoryRepository.set_embedding(
        session, bills.id, embedding=pack_embedding([0.99, 0.05]), model="m", dim=2
    )
    # More documents on "Invoice" -> merge "Bills" into it.
    await _assign_document(session, invoice.id, "sha256:1", confidence=0.9)
    await _assign_document(session, invoice.id, "sha256:2", confidence=0.9)
    await _assign_document(session, bills.id, "sha256:3", confidence=0.9)
    await session.flush()

    result = await ConsolidationService(session).proposals()

    assert any(
        proposal.source_id == bills.id and proposal.target_id == invoice.id
        for proposal in result.merges
    )
    assert result.renames == []


async def test_llm_consolidation_proposals_are_parsed(session: AsyncSession):
    billing = await CategoryRepository.create(session, name="Billing")
    finance = await CategoryRepository.create(session, name="Finance")
    provider = FakeProvider(
        {
            "merges": [
                {
                    "source_id": billing.id,
                    "target_id": finance.id,
                    "reason": "billing is finance",
                }
            ],
            "renames": [
                {
                    "category_id": finance.id,
                    "proposed_name": "Finance & Billing",
                    "reason": "clearer",
                }
            ],
        }
    )

    result = await ConsolidationService(session, provider).proposals()

    assert any(
        proposal.source_id == billing.id
        and proposal.target_id == finance.id
        and proposal.origin == "llm"
        for proposal in result.merges
    )
    assert any(
        proposal.category_id == finance.id
        and proposal.proposed_name == "Finance & Billing"
        for proposal in result.renames
    )
    assert provider.prompts  # the outline was sent
