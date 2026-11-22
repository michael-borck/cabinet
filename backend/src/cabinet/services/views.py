"""Saved live views over facts + concepts (Phase 4a, design §4).

A view is a query, not a container: these helpers build SQL filters from a
``ViewQuery``, count version clusters once, and return one representative per
cluster in collapsed mode. No extracted text is read and nothing is copied.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cabinet.db.models import (
    Category,
    ContentFact,
    Document,
    DocumentCategory,
    File,
    FileCategory,
)
from cabinet.db.repositories import TextIndexRepository
from cabinet.schemas import ViewQuery

_SORT_COLUMNS = {
    "file_modified_at": File.file_modified_at,
    "filename": File.filename,
    "size_bytes": File.size_bytes,
    "created_at": File.created_at,
}


@dataclass
class ViewPage:
    files: list[File] = field(default_factory=list)
    total: int = 0
    version_counts: dict[int, int] = field(default_factory=dict)


def _cluster_key():
    """Group key: cluster id, else a negative document/file id (singleton)."""
    return func.coalesce(Document.cluster_id, -Document.id, -File.id)


def _file_options():
    return (
        selectinload(File.categories).joinedload(FileCategory.category),
        selectinload(File.document)
        .selectinload(Document.categories)
        .joinedload(DocumentCategory.category),
    )


class ViewQueryService:
    """Evaluates a ``ViewQuery`` against the database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(
        self, query: ViewQuery, *, offset: int = 0, limit: int = 50
    ) -> ViewPage:
        conditions = await self._conditions(query)
        collapse = query.clusters != "separate"

        total = await self._count(conditions, collapse)
        if total == 0:
            return ViewPage(files=[], total=0)

        if collapse:
            files = await self._collapsed_page(
                conditions, query, offset=offset, limit=limit
            )
        else:
            files = await self._separate_page(
                conditions, query, offset=offset, limit=limit
            )

        return ViewPage(
            files=files,
            total=total,
            version_counts=await self._version_counts(files),
        )

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    async def _conditions(self, query: ViewQuery) -> list:
        conditions: list = []

        if query.concepts and query.concepts.ids:
            groups = await self._concept_groups(
                query.concepts.ids, query.concepts.include_descendants
            )
            # Each selected concept is one filter over its subtree; "all"
            # intersects the selected subtrees, "any" unions them.
            group_conditions = [
                or_(*[_concept_exists(cid) for cid in group])
                for group in groups
                if group
            ]
            if group_conditions:
                conditions.append(
                    and_(*group_conditions)
                    if query.concepts.match == "all"
                    else or_(*group_conditions)
                )

        if query.doc_types:
            wanted = [value.strip().lower() for value in query.doc_types if value.strip()]
            if wanted:
                conditions.append(
                    func.lower(ContentFact.doc_type).in_(wanted)
                )

        if query.topics:
            topics = [value.strip() for value in query.topics if value.strip()]
            if topics:
                # Topics are a JSON list; match quoted values to avoid
                # substring false positives like "bill" vs "billing".
                conditions.append(
                    or_(
                        *[
                            cast(ContentFact.topics, String).ilike(f'%"{topic}"%')
                            for topic in topics
                        ]
                    )
                )

        if query.sources:
            conditions.append(File.source_id.in_(query.sources))

        text = (query.text or "").strip()
        if text:
            pattern = f"%{text}%"
            text_matches = [
                File.filename.ilike(pattern),
                File.path.ilike(pattern),
                ContentFact.summary.ilike(pattern),
                ContentFact.doc_type.ilike(pattern),
                cast(ContentFact.topics, String).ilike(pattern),
            ]
            hashes = await TextIndexRepository.search_hashes(self._session, text)
            if hashes:
                text_matches.append(Document.content_hash.in_(hashes))
            conditions.append(or_(*text_matches))

        return conditions

    async def _concept_groups(
        self, ids: list[int], include_descendants: bool
    ) -> list[set[int]]:
        """One expanded id set per selected concept (for any/all semantics)."""
        if not include_descendants:
            return [{category_id} for category_id in ids]

        rows = (
            await self._session.execute(
                select(Category.id, Category.parent_id)
            )
        ).all()
        children: dict[int, list[int]] = {}
        for category_id, parent_id in rows:
            if parent_id is not None:
                children.setdefault(parent_id, []).append(category_id)

        groups: list[set[int]] = []
        for root in ids:
            expanded: set[int] = set()
            stack = [root]
            while stack:
                category_id = stack.pop()
                if category_id in expanded:
                    continue
                expanded.add(category_id)
                stack.extend(children.get(category_id, []))
            groups.append(expanded)
        return groups

    # ------------------------------------------------------------------
    # Counting / fetching
    # ------------------------------------------------------------------

    async def _count(self, conditions: list, collapse: bool) -> int:
        if collapse:
            statement = (
                select(func.count(func.distinct(_cluster_key())))
                .select_from(File)
                .outerjoin(Document, File.document_id == Document.id)
                .outerjoin(
                    ContentFact,
                    ContentFact.content_hash == Document.content_hash,
                )
                .where(*conditions)
            )
        else:
            statement = (
                select(func.count(File.id))
                .select_from(File)
                .outerjoin(Document, File.document_id == Document.id)
                .outerjoin(
                    ContentFact,
                    ContentFact.content_hash == Document.content_hash,
                )
                .where(*conditions)
            )
        return int((await self._session.execute(statement)).scalar() or 0)

    def _sort_expression(self, query: ViewQuery):
        return _SORT_COLUMNS.get(query.sort_by, File.file_modified_at)

    def _sort_direction(self, query: ViewQuery, expression):
        return expression.asc() if query.sort_dir == "asc" else expression.desc()

    async def _collapsed_page(
        self, conditions: list, query: ViewQuery, *, offset: int, limit: int
    ) -> list[File]:
        key = _cluster_key()
        sort_expression = self._sort_expression(query)
        window_order = self._sort_direction(query, sort_expression)

        ranked = (
            select(
                File.id.label("file_id"),
                key.label("cluster_key"),
                sort_expression.label("sort_value"),
                func.row_number()
                .over(partition_by=key, order_by=window_order)
                .label("rn"),
            )
            .select_from(File)
            .outerjoin(Document, File.document_id == Document.id)
            .outerjoin(
                ContentFact, ContentFact.content_hash == Document.content_hash
            )
            .where(*conditions)
            .subquery()
        )

        ids_statement = (
            select(ranked.c.file_id)
            .where(ranked.c.rn == 1)
            .order_by(
                ranked.c.sort_value.asc()
                if query.sort_dir == "asc"
                else ranked.c.sort_value.desc(),
                ranked.c.file_id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
        ids = [row[0] for row in (await self._session.execute(ids_statement)).all()]
        if not ids:
            return []
        return await self._files_by_ids(ids)

    async def _separate_page(
        self, conditions: list, query: ViewQuery, *, offset: int, limit: int
    ) -> list[File]:
        sort_expression = self._sort_expression(query)
        statement = (
            select(File)
            .outerjoin(Document, File.document_id == Document.id)
            .outerjoin(
                ContentFact, ContentFact.content_hash == Document.content_hash
            )
            .where(*conditions)
            .options(*_file_options())
            .order_by(
                self._sort_direction(query, sort_expression),
                File.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def _files_by_ids(self, ids: list[int]) -> list[File]:
        rows = (
            await self._session.execute(
                select(File)
                .where(File.id.in_(ids))
                .options(*_file_options())
            )
        ).scalars().all()
        by_id = {file.id: file for file in rows}
        return [by_id[file_id] for file_id in ids if file_id in by_id]

    async def _version_counts(self, files: list[File]) -> dict[int, int]:
        cluster_ids = {
            file.document.cluster_id
            for file in files
            if file.document is not None and file.document.cluster_id is not None
        }
        counts: dict[int, int] = {}
        if cluster_ids:
            rows = (
                await self._session.execute(
                    select(Document.cluster_id, func.count(Document.id))
                    .where(Document.cluster_id.in_(cluster_ids))
                    .group_by(Document.cluster_id)
                )
            ).all()
            counts = {cluster_id: count for cluster_id, count in rows}

        return {
            file.id: (
                counts.get(file.document.cluster_id, 1)
                if file.document is not None
                and file.document.cluster_id is not None
                else 1
            )
            for file in files
        }


def _concept_exists(category_id: int):
    """A file is in a concept via its document or a per-instance addition."""
    return or_(
        select(DocumentCategory.document_id)
        .where(
            DocumentCategory.category_id == category_id,
            DocumentCategory.document_id == File.document_id,
        )
        .exists(),
        select(FileCategory.file_id)
        .where(
            FileCategory.category_id == category_id,
            FileCategory.file_id == File.id,
        )
        .exists(),
    )
