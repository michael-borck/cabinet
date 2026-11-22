"""Phase 3d taxonomy drift metrics."""

from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, DocumentCategory
from cabinet.db.repositories import CategoryRepository
from cabinet.services.insights import TaxonomyInsightsService
from cabinet.utils.embeddings import pack_embedding


async def _document(session: AsyncSession, digest: str) -> Document:
    document = Document(content_hash=digest, status="categorized")
    session.add(document)
    await session.flush()
    return document


async def test_insights_metrics(session: AsyncSession):
    invoice = await CategoryRepository.create(session, name="Invoice")
    bills = await CategoryRepository.create(session, name="Bills")
    travel = await CategoryRepository.create(session, name="Travel")
    await CategoryRepository.set_embedding(
        session, invoice.id, embedding=pack_embedding([1.0, 0.0]), model="m", dim=2
    )
    await CategoryRepository.set_embedding(
        session, bills.id, embedding=pack_embedding([0.99, 0.05]), model="m", dim=2
    )

    for index, (category, confidence) in enumerate(
        (
            (invoice, 0.95),
            (invoice, 0.92),
            (bills, 0.8),
            (travel, 0.6),
        )
    ):
        document = await _document(session, f"sha256:{index}")
        session.add(
            DocumentCategory(
                document_id=document.id,
                category_id=category.id,
                confidence=confidence,
                is_manual=False,
            )
        )
    await session.flush()

    insights = await TaxonomyInsightsService(session).insights()

    assert insights.total_documents == 4
    assert insights.total_concepts == 3
    assert insights.approved_concepts == 3
    assert insights.singleton_rate == round(2 / 3, 3)
    assert insights.concepts_per_100_documents == 75.0
    assert insights.possible_duplicate_pairs == 1
    assert insights.confidence_buckets["very_high"] == 2
    assert insights.confidence_buckets["high"] == 1
    assert insights.confidence_buckets["medium"] == 1
