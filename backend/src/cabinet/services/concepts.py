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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Category
from cabinet.db.repositories import CategoryRepository
from cabinet.services.embeddings import EmbeddingService
from cabinet.utils.concepts import concept_embedding_text, normalize_concept
from cabinet.utils.embeddings import cosine_similarity, pack_embedding, unpack_embedding
from cabinet.utils.text import make_slug

logger = logging.getLogger(__name__)

# D11: reuse the Phase 2b embedding config.
AUTO_ALIAS_THRESHOLD = 0.92
REVIEW_BAND_THRESHOLD = 0.82


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

        # 5. New provisional concept.
        logger.info("Creating provisional concept %r", cleaned)
        return await CategoryRepository.create(
            self._session,
            name=cleaned,
            parent_id=parent_id,
            is_ai_suggested=True,
            status="provisional",
            origin="ai",
        )

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
