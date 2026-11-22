"""Taxonomy drift metrics (Phase 3d, design §9)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, DocumentCategory
from cabinet.db.repositories import CategoryRepository
from cabinet.schemas import TaxonomyInsights
from cabinet.utils.concepts import normalize_concept
from cabinet.utils.embeddings import cosine_similarity, unpack_embedding

DUPLICATE_SIMILARITY_THRESHOLD = 0.9


class TaxonomyInsightsService:
    """Read-only drift metrics computed from existing rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insights(self) -> TaxonomyInsights:
        categories = await CategoryRepository.list_all(self._session)
        non_merged = [
            category for category in categories if category.status != "merged"
        ]
        counts = {
            row["category"].id: row["file_count"]
            for row in await CategoryRepository.get_flat_with_counts(self._session)
        }

        total_documents = (
            await self._session.execute(select(func.count(Document.id)))
        ).scalar() or 0

        with_documents = [
            category
            for category in non_merged
            if counts.get(category.id, 0) > 0
        ]
        singletons = sum(
            1 for category in with_documents if counts[category.id] == 1
        )
        singleton_rate = (
            singletons / len(with_documents) if with_documents else 0.0
        )
        concepts_per_100 = (
            len(non_merged) / total_documents * 100 if total_documents else 0.0
        )

        return TaxonomyInsights(
            total_documents=total_documents,
            total_concepts=len(non_merged),
            approved_concepts=sum(
                1 for category in non_merged if category.status == "approved"
            ),
            provisional_concepts=sum(
                1 for category in non_merged if category.status == "provisional"
            ),
            merged_concepts=sum(
                1 for category in categories if category.status == "merged"
            ),
            singleton_rate=round(singleton_rate, 3),
            concepts_per_100_documents=round(concepts_per_100, 2),
            possible_duplicate_pairs=_duplicate_pairs(non_merged),
            aliases_total=sum(len(category.aliases) for category in non_merged),
            concepts_with_aliases=sum(
                1 for category in non_merged if category.aliases
            ),
            confidence_buckets=await self._confidence_buckets(),
        )

    async def _confidence_buckets(self) -> dict[str, int]:
        values = (
            await self._session.execute(
                select(DocumentCategory.confidence).where(
                    DocumentCategory.is_manual == False  # noqa: E712
                )
            )
        ).scalars().all()

        buckets = {
            "unknown": 0,
            "low": 0,  # < 0.5
            "medium": 0,  # 0.5-0.7
            "high": 0,  # 0.7-0.9
            "very_high": 0,  # >= 0.9
        }
        for value in values:
            if value is None:
                buckets["unknown"] += 1
            elif value < 0.5:
                buckets["low"] += 1
            elif value < 0.7:
                buckets["medium"] += 1
            elif value < 0.9:
                buckets["high"] += 1
            else:
                buckets["very_high"] += 1
        return buckets


def _duplicate_pairs(categories: list) -> int:
    """Count unique concept pairs that look like duplicates."""
    count = 0
    for index, first in enumerate(categories):
        for second in categories[index + 1 :]:
            if normalize_concept(first.name) == normalize_concept(second.name):
                count += 1
                continue
            if first.embedding and second.embedding:
                similarity = cosine_similarity(
                    unpack_embedding(first.embedding),
                    unpack_embedding(second.embedding),
                )
                if similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
                    count += 1
    return count
