"""Concept resolution for Phase 3a (design §4).

Resolve an LLM- or user-proposed name in a fixed order:

1. exact slug under the intended parent, then globally;
2. alias (globally unique normalized key, D9);
3. stemmed/plural match against canonical names and aliases;
4. embedding similarity against ``name + definition + aliases`` (D11);
5. otherwise create a *provisional* concept.

Embeddings are optional: without an embedding config, steps 1–3 and
provisional creation still work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Category, CategoryExample
from cabinet.db.repositories import CategoryRepository, ContentFactRepository
from cabinet.services.embeddings import EmbeddingService
from cabinet.utils.concepts import concept_embedding_text, normalize_concept
from cabinet.utils.embeddings import cosine_similarity, pack_embedding, unpack_embedding
from cabinet.utils.text import make_slug

logger = logging.getLogger(__name__)

# D11: reuse the Phase 2b embedding config.
AUTO_ALIAS_THRESHOLD = 0.92
REVIEW_BAND_THRESHOLD = 0.82


@dataclass
class ConceptCandidate:
    """A concept offered to the assignment prompt."""

    category: Category
    similarity: float
    examples: list[CategoryExample] = field(default_factory=list)


def concept_path(category: Category, by_id: dict[int, Category]) -> str:
    """Return the full "Parent > Child" path for a concept."""
    parts: list[str] = []
    current: Category | None = category
    seen: set[int] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        parts.append(current.name)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return " > ".join(reversed(parts))


class ConceptResolver:
    """Resolves names to canonical concepts, creating provisional ones."""

    def __init__(
        self,
        session: AsyncSession,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self._session = session
        self._embeddings = embedding_service
        self._name_vectors: dict[str, list[float]] = {}
        self._backfilled = False

    async def resolve(
        self, name: str, *, parent_id: int | None = None
    ) -> Category:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Empty concept name")

        # 1. Exact slug under the intended parent, then anywhere.
        category = await self._find_by_slug(make_slug(cleaned), parent_id)
        if category is not None:
            return await self._follow_merge(category)

        # 2. Alias.
        normalized = normalize_concept(cleaned)
        category = await CategoryRepository.get_by_normalized_alias(
            self._session, normalized
        )
        if category is not None:
            return await self._follow_merge(category)

        # 3. Stemmed/plural match.
        category = await self._stemmed_match(normalized)
        if category is not None:
            return await self._follow_merge(category)

        # 4. Embedding similarity (auto-alias at the top of the band).
        category = await self._embedding_match(cleaned)
        if category is not None:
            return await self._follow_merge(category)

        # 5. New provisional concept (embed it immediately when possible so
        # the review inbox can rank possible duplicates).
        logger.info("Creating provisional concept %r", cleaned)
        created = await CategoryRepository.create(
            self._session,
            name=cleaned,
            parent_id=parent_id,
            is_ai_suggested=True,
            status="provisional",
            origin="ai",
        )
        if self._embeddings is not None:
            vector = await self._name_vector(cleaned)
            if vector:
                await CategoryRepository.set_embedding(
                    self._session,
                    created.id,
                    embedding=pack_embedding(vector),
                    model=self._embeddings.model,
                    dim=len(vector),
                )
        return created

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    async def _find_by_slug(
        self, slug: str, parent_id: int | None
    ) -> Category | None:
        if parent_id is not None:
            result = await self._session.execute(
                select(Category)
                .where(Category.slug == slug, Category.parent_id == parent_id)
                .limit(1)
            )
            found = result.scalar_one_or_none()
            if found is not None:
                return found
        result = await self._session.execute(
            select(Category).where(Category.slug == slug).limit(1)
        )
        return result.scalar_one_or_none()

    async def _stemmed_match(self, normalized: str) -> Category | None:
        if not normalized:
            return None
        categories = await CategoryRepository.list_all(self._session)
        fallback: Category | None = None
        for category in categories:
            if category.status == "merged":
                continue
            if normalize_concept(category.name) == normalized:
                if category.status == "approved":
                    return category
                fallback = fallback or category
            for alias in category.aliases:
                if alias.normalized == normalized:
                    if category.status == "approved":
                        return category
                    fallback = fallback or category
        return fallback

    async def _embedding_match(self, name: str) -> Category | None:
        if self._embeddings is None:
            return None
        await self._backfill_concept_embeddings()

        vector = await self._name_vector(name)
        if not vector:
            return None

        categories = await CategoryRepository.list_all(self._session)
        best: tuple[float, Category] | None = None
        for category in categories:
            if category.status == "merged" or not category.embedding:
                continue
            similarity = cosine_similarity(
                vector, unpack_embedding(category.embedding)
            )
            if best is None or similarity > best[0]:
                best = (similarity, category)

        if best is None:
            return None
        similarity, category = best
        if similarity >= AUTO_ALIAS_THRESHOLD:
            try:
                await CategoryRepository.add_alias(
                    self._session, category.id, name, source="ai"
                )
            except ValueError:
                # The name is already reserved by another concept; resolution
                # will meet it on the next slug/alias pass.
                logger.debug("Auto-alias %r conflicted; not added", name)
            return category
        if similarity >= REVIEW_BAND_THRESHOLD:
            # Reuse the closer concept now; the duplicate is queued for the
            # review inbox in 3b rather than aliased automatically.
            return category
        return None

    async def _backfill_concept_embeddings(self) -> None:
        """Embed concepts missing a vector for the current model (3a)."""
        if self._backfilled or self._embeddings is None:
            return
        self._backfilled = True

        categories = await CategoryRepository.list_all(self._session)
        targets = [
            category
            for category in categories
            if category.status != "merged"
            and (
                not category.embedding
                or category.embedding_model != self._embeddings.model
            )
        ]
        if not targets:
            return

        texts = [
            concept_embedding_text(
                category.name,
                category.definition,
                [alias.alias for alias in category.aliases],
            )
            for category in targets
        ]
        try:
            vectors = await self._embeddings.embed_texts(texts)
        except Exception:
            logger.warning("Concept embedding backfill failed", exc_info=True)
            return

        for category, vector in zip(targets, vectors, strict=True):
            if not vector:
                continue
            category.embedding = pack_embedding(vector)
            category.embedding_model = self._embeddings.model
            category.embedding_dim = len(vector)
        await self._session.flush()

    async def _name_vector(self, name: str) -> list[float] | None:
        key = normalize_concept(name)
        if key in self._name_vectors:
            return self._name_vectors[key] or None
        assert self._embeddings is not None
        try:
            vectors = await self._embeddings.embed_texts([name])
        except Exception:
            logger.warning("Embedding proposed concept name failed", exc_info=True)
            self._name_vectors[key] = []
            return None
        vector = vectors[0] if vectors else []
        self._name_vectors[key] = vector
        return vector or None

    async def candidates_for_embedding(
        self, vector: list[float], *, limit: int = 20
    ) -> list[ConceptCandidate] | None:
        """Nearest approved concepts for one document (Phase 3b).

        Returns ``None`` when no approved concept has an embedding, which
        tells the caller to fall back to the full-tree prompt.
        """
        if not vector:
            return None
        categories = await CategoryRepository.list_all(self._session)
        scored: list[tuple[float, Category]] = []
        for category in categories:
            if category.status != "approved" or not category.embedding:
                continue
            scored.append(
                (
                    cosine_similarity(vector, unpack_embedding(category.embedding)),
                    category,
                )
            )
        if not scored:
            return None
        scored.sort(key=lambda pair: pair[0], reverse=True)

        candidates: list[ConceptCandidate] = []
        for similarity, category in scored[:limit]:
            examples = sorted(
                [
                    example
                    for example in category.examples
                    if example.source != "manual_negative"
                ],
                key=lambda example: 0 if example.source == "manual" else 1,
            )
            candidates.append(
                ConceptCandidate(
                    category=category,
                    similarity=similarity,
                    examples=examples[:2],
                )
            )
        return candidates

    async def _follow_merge(self, category: Category) -> Category:
        """Follow ``merged_into`` links so assignments land on the winner."""
        seen: set[int] = set()
        current = category
        while (
            current.status == "merged"
            and current.merged_into_id is not None
            and current.id not in seen
        ):
            seen.add(current.id)
            target = await self._session.get(Category, current.merged_into_id)
            if target is None:
                break
            current = target
        return current


# ---------------------------------------------------------------------------
# Review inbox (Phase 3b)
# ---------------------------------------------------------------------------


@dataclass
class ReviewSample:
    file_id: int
    filename: str
    summary: str | None = None


@dataclass
class ReviewDuplicate:
    category_id: int
    name: str
    path: str
    similarity: float


@dataclass
class ReviewItem:
    category: Category
    path: str
    document_count: int
    samples: list[ReviewSample] = field(default_factory=list)
    possible_duplicates: list[ReviewDuplicate] = field(default_factory=list)


def possible_duplicates(
    category: Category,
    candidates: list[Category],
    by_id: dict[int, Category],
    *,
    threshold: float = REVIEW_BAND_THRESHOLD,
    limit: int = 3,
) -> list[ReviewDuplicate]:
    """Rank approved concepts that look like *category* (embedding + name)."""
    target_name = normalize_concept(category.name)
    results: list[ReviewDuplicate] = []
    for other in candidates:
        if other.id == category.id or other.status != "approved":
            continue
        similarity = 0.0
        if category.embedding and other.embedding:
            similarity = cosine_similarity(
                unpack_embedding(category.embedding),
                unpack_embedding(other.embedding),
            )
        name_match = normalize_concept(other.name) == target_name
        score = max(similarity, 1.0 if name_match else 0.0)
        if score < threshold:
            continue
        results.append(
            ReviewDuplicate(
                category_id=other.id,
                name=other.name,
                path=concept_path(other, by_id),
                similarity=round(score, 3),
            )
        )
    results.sort(key=lambda item: item.similarity, reverse=True)
    return results[:limit]


class TaxonomyReviewService:
    """Builds the provisional-concept inbox and applies review actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def items(self) -> list[ReviewItem]:
        categories = await CategoryRepository.list_all(self._session)
        by_id = {category.id: category for category in categories}
        approved = [category for category in categories if category.status == "approved"]

        results: list[ReviewItem] = []
        for category in categories:
            if category.status != "provisional":
                continue
            count = await CategoryRepository.get_document_count(
                self._session, category.id
            )
            sample_rows = await CategoryRepository.get_sample_files(
                self._session, category.id, limit=3
            )
            samples: list[ReviewSample] = []
            for file_id, filename, content_hash in sample_rows:
                summary: str | None = None
                if content_hash:
                    fact = await ContentFactRepository.get(
                        self._session, content_hash
                    )
                    summary = fact.summary if fact is not None else None
                samples.append(
                    ReviewSample(
                        file_id=file_id, filename=filename, summary=summary
                    )
                )
            results.append(
                ReviewItem(
                    category=category,
                    path=concept_path(category, by_id),
                    document_count=count,
                    samples=samples,
                    possible_duplicates=possible_duplicates(
                        category, approved, by_id
                    ),
                )
            )
        return results

    async def approve(
        self, category_id: int, definition: str | None = None
    ) -> Category | None:
        category = await CategoryRepository.get_with_relations(
            self._session, category_id
        )
        if category is None or category.status != "provisional":
            return None
        category.status = "approved"
        if definition is not None:
            category.definition = definition
        await self._session.flush()
        return category

    async def reject(self, category_id: int) -> bool:
        category = await CategoryRepository.get_by_id(self._session, category_id)
        if category is None or category.status != "provisional":
            return False
        await self._session.delete(category)
        await self._session.flush()
        return True
