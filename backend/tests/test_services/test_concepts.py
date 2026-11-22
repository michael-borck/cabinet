"""Phase 3a concept resolution (design §4)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.repositories import CategoryRepository
from cabinet.services.concepts import ConceptResolver
from cabinet.utils.concepts import normalize_concept


class FakeEmbeddingService:
    """Duck-typed EmbeddingService: fixed model, text->vector map."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.model = "fake-embed"
        self.mapping = mapping

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.mapping.get(text, [0.0, 1.0]) for text in texts]


def test_normalize_concept_handles_plurals_and_punctuation():
    assert normalize_concept("Invoices") == "invoice"
    assert normalize_concept("Finances") == "finance"
    assert normalize_concept("Billing") == "billing"
    assert normalize_concept("Q1 Reports!") == "q1 report"
    assert normalize_concept("status") == "status"


async def test_resolve_exact_slug(session: AsyncSession):
    existing = await CategoryRepository.create(session, name="Finance")
    resolver = ConceptResolver(session)

    found = await resolver.resolve("Finance")

    assert found.id == existing.id


async def test_resolve_alias(session: AsyncSession):
    billing = await CategoryRepository.create(session, name="Billing")
    await CategoryRepository.add_alias(session, billing.id, "Invoices")

    found = await ConceptResolver(session).resolve("Invoices")

    assert found.id == billing.id


async def test_resolve_stemmed_plural(session: AsyncSession):
    invoice = await CategoryRepository.create(session, name="Invoice")

    found = await ConceptResolver(session).resolve("Invoices")

    assert found.id == invoice.id


async def test_resolve_creates_provisional_concept(session: AsyncSession):
    found = await ConceptResolver(session).resolve("Zebra Reports")

    assert found.status == "provisional"
    assert found.origin == "ai"
    assert found.is_ai_suggested is True
    assert found.slug == "zebra-reports"


async def test_resolve_embedding_auto_alias(session: AsyncSession):
    billing = await CategoryRepository.create(session, name="Billing")
    embeddings = FakeEmbeddingService(
        {
            "Billing": [1.0, 0.0],
            "Invoicing": [1.0, 0.0],
        }
    )

    found = await ConceptResolver(session, embeddings).resolve("Invoicing")

    assert found.id == billing.id
    await session.refresh(billing, attribute_names=["aliases"])
    assert "Invoicing" in {alias.alias for alias in billing.aliases}


async def test_resolve_embedding_review_band_reuses_without_alias(
    session: AsyncSession,
):
    billing = await CategoryRepository.create(session, name="Billing")
    embeddings = FakeEmbeddingService(
        {
            "Billing": [1.0, 0.0],
            "Fees": [0.85, 0.5268],  # cosine ~0.85, inside the review band
        }
    )

    found = await ConceptResolver(session, embeddings).resolve("Fees")

    assert found.id == billing.id
    await session.refresh(billing, attribute_names=["aliases"])
    assert billing.aliases == []


async def test_merged_concept_follows_winner(session: AsyncSession):
    old = await CategoryRepository.create(session, name="Old Name")
    winner = await CategoryRepository.create(session, name="New Name")
    old.status = "merged"
    old.merged_into_id = winner.id
    await session.flush()

    found = await ConceptResolver(session).resolve("Old Name")

    assert found.id == winner.id


async def test_alias_conflicts_are_rejected(session: AsyncSession):
    first = await CategoryRepository.create(session, name="Billing")
    second = await CategoryRepository.create(session, name="Finance")
    await CategoryRepository.add_alias(session, first.id, "Reports")

    with pytest.raises(ValueError):
        await CategoryRepository.add_alias(session, second.id, "Reports")
