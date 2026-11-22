"""Data-access repositories for the file-sorter application.

Every public method receives an ``AsyncSession`` as its first argument so that
callers control transaction boundaries.  All queries use SQLAlchemy 2.0-style
``select()`` / ``insert()`` / ``update()`` / ``delete()`` expressions.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from cabinet.db.models import (
    Category,
    CategoryAlias,
    CategoryExample,
    ContentFact,
    Document,
    DocumentCategory,
    File,
    FileCategory,
    Job,
    LLMConfig,
    Source,
)
from cabinet.utils.concepts import normalize_concept
from cabinet.utils.fingerprint import fingerprint_similarity, stem_key
from cabinet.utils.hashing import prefixed_hash
from cabinet.utils.volumes import describe_source_root

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    # Naive UTC: SQLite DateTime columns are timezone-less, and mixing aware
    # and naive values makes equality comparisons silently always-unequal.
    return datetime.now(UTC).replace(tzinfo=None)


_FILE_SORT_COLUMNS = {
    "id",
    "filename",
    "extension",
    "size_bytes",
    "status",
    "file_modified_at",
    "created_at",
    "updated_at",
}


def _slugify(value: str) -> str:
    """Produce a URL-safe slug from *value*."""
    slug = value.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


# ---------------------------------------------------------------------------
# SourceRepository
# ---------------------------------------------------------------------------


class SourceRepository:
    """CRUD operations for :class:`Source` rows."""

    @staticmethod
    async def list_all(session: AsyncSession) -> Sequence[Source]:
        result = await session.execute(select(Source).order_by(Source.created_at.desc()))
        return result.scalars().all()

    @staticmethod
    async def get_by_id(session: AsyncSession, source_id: int) -> Source | None:
        return await session.get(Source, source_id)

    @staticmethod
    async def list_watched(session: AsyncSession) -> Sequence[Source]:
        result = await session.execute(
            select(Source).where(Source.watch_enabled == True)  # noqa: E712
        )
        return result.scalars().all()

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        path: str,
        label: str | None = None,
        recursive: bool = True,
        exclude_patterns: list[str] | None = None,
        watch_enabled: bool = False,
        store_facts: bool = True,
        store_text_index: bool = False,
    ) -> Source:
        volume_id, volume_rel_path = describe_source_root(path)
        source = Source(
            path=path,
            label=label,
            recursive=recursive,
            exclude_patterns=exclude_patterns or None,
            watch_enabled=watch_enabled,
            store_facts=store_facts,
            store_text_index=store_text_index,
            volume_id=volume_id,
            volume_rel_path=volume_rel_path,
        )
        session.add(source)
        await session.flush()
        return source

    @staticmethod
    async def update(
        session: AsyncSession, source_id: int, **fields: object
    ) -> Source | None:
        """Apply the given fields to a source; returns None when it doesn't exist."""
        source = await session.get(Source, source_id)
        if source is None:
            return None
        for name, value in fields.items():
            setattr(source, name, value)
        await session.flush()
        return source

    @staticmethod
    async def delete(session: AsyncSession, source_id: int) -> bool:
        """Delete a source and cascade-delete its files.

        Returns ``True`` if the source existed and was deleted.
        """
        source = await session.get(Source, source_id)
        if source is None:
            return False
        await session.delete(source)
        await session.flush()
        return True


# ---------------------------------------------------------------------------
# DocumentRepository (Phase 1a identity)
# ---------------------------------------------------------------------------

# Furthest-along pipeline status wins when documents merge.
_STATUS_RANK = {"pending": 0, "error": 1, "extracted": 2, "categorized": 3}


def _furthest_status(a: str, b: str) -> str:
    return a if _STATUS_RANK.get(a, 0) >= _STATUS_RANK.get(b, 0) else b


def effective_categories(file: File) -> list[tuple[Category, float | None, bool]]:
    """Merged (category, confidence, is_manual) rows for one file instance.

    Document-level assignments first, then per-instance additions for
    categories the document doesn't already carry. Requires
    ``file.categories`` and ``file.document.categories`` to be loaded.
    """
    merged: dict[int, tuple[Category, float | None, bool]] = {}
    if file.document is not None:
        for dc in file.document.categories:
            merged[dc.category_id] = (dc.category, dc.confidence, dc.is_manual)
    for fc in file.categories:
        merged.setdefault(fc.category_id, (fc.category, fc.confidence, fc.is_manual))
    return list(merged.values())


class DocumentRepository:
    """Identity operations for :class:`Document` rows (design §3)."""

    @staticmethod
    async def get_by_hash(
        session: AsyncSession, content_hash: str
    ) -> Document | None:
        """Find a document by hash, accepting legacy bare-hex storage."""
        prefixed = prefixed_hash(content_hash)
        bare = prefixed.split(":", 1)[1]
        result = await session.execute(
            select(Document).where(Document.content_hash.in_((prefixed, bare)))
        )
        doc = result.scalar_one_or_none()
        if doc is not None and doc.content_hash != prefixed:
            doc.content_hash = prefixed  # normalise legacy storage in passing
        return doc

    @staticmethod
    async def ensure_for_file(session: AsyncSession, file: File) -> Document:
        """Return the file's document, creating/reusing one if needed.

        A stored hash reuses (or creates) the hashed document; no hash yet
        means a fresh *provisional* document awaits lazy backfill merging.
        """
        if file.document_id is not None:
            doc = await session.get(Document, file.document_id)
            if doc is not None:
                return doc
        if file.content_hash:
            return await DocumentRepository.attach_hash(
                session, file, prefixed_hash(file.content_hash)
            )
        status = file.status if file.status in _STATUS_RANK else "pending"
        doc = Document(content_hash=None, status=status)
        session.add(doc)
        await session.flush()
        file.document_id = doc.id
        return doc

    @staticmethod
    async def attach_hash(
        session: AsyncSession, file: File, content_hash: str
    ) -> Document:
        """Point *file* at the document for *content_hash* (prefixed form).

        Handles the three shapes from design §3.1/§3.2: adopting the hash
        into a provisional document, merging a provisional document into an
        existing hashed one, and repointing after in-place content change.
        """
        prefixed = prefixed_hash(content_hash)
        existing = await DocumentRepository.get_by_hash(session, prefixed)
        current = (
            await session.get(Document, file.document_id)
            if file.document_id is not None
            else None
        )

        if existing is not None:
            if (
                current is not None
                and current.id != existing.id
                and current.content_hash is None
            ):
                await DocumentRepository.merge_documents(session, current, existing)
            # Hashed current with a different hash = content changed at this
            # path; the old document keeps its other instances.
            file.document_id = existing.id
            file.content_hash = prefixed
            return existing

        if current is not None and current.content_hash is None:
            current.content_hash = prefixed
            file.content_hash = prefixed
            return current

        doc = Document(content_hash=prefixed, status="pending")
        session.add(doc)
        await session.flush()
        file.document_id = doc.id
        file.content_hash = prefixed
        return doc

    @staticmethod
    async def merge_documents(
        session: AsyncSession, source: Document, target: Document
    ) -> Document:
        """Fold *source* into *target*: instances, categories, status.

        Only ever called on exact hash equality (provisional documents
        discovered to be duplicates); fuzzy similarity never merges.
        """
        if source.id == target.id:
            return target

        instances = (
            await session.execute(
                select(File).where(File.document_id == source.id)
            )
        ).scalars().all()
        for inst in instances:
            inst.document_id = target.id
            if target.content_hash:
                inst.content_hash = target.content_hash

        target_cats = {
            dc.category_id: dc
            for dc in (
                await session.execute(
                    select(DocumentCategory).where(
                        DocumentCategory.document_id == target.id
                    )
                )
            ).scalars().all()
        }
        source_cats = (
            await session.execute(
                select(DocumentCategory).where(
                    DocumentCategory.document_id == source.id
                )
            )
        ).scalars().all()
        for dc in source_cats:
            keep = target_cats.get(dc.category_id)
            if keep is None:
                dc.document_id = target.id
            else:
                # Manual beats AI; keep the higher confidence.
                keep.is_manual = keep.is_manual or dc.is_manual
                confs = [c for c in (keep.confidence, dc.confidence) if c is not None]
                keep.confidence = max(confs) if confs else None
                await session.delete(dc)

        target.status = _furthest_status(target.status, source.status)
        await session.flush()
        await session.delete(source)
        await session.flush()
        return target

    @staticmethod
    async def assign_cluster(
        session: AsyncSession,
        document: Document,
        *,
        fingerprint: str,
        file_path: str,
        filename: str,
        source_id: int,
        threshold: float = 0.9,
    ) -> int | None:
        """Group *document* with corroborated sibling versions (design §3.4).

        Candidates must be in the same source and folder, share a normalised
        filename stem, and have a SimHash similarity of at least *threshold*.
        The cluster key is the earliest member's document id; the document is
        grouped, never merged. Returns the cluster id or None.
        """
        parent_dir = os.path.dirname(file_path)
        prefix = parent_dir.rstrip(os.sep) + os.sep
        rows = (
            await session.execute(
                select(
                    Document.id,
                    Document.cluster_id,
                    ContentFact.fingerprint,
                    File.path,
                    File.filename,
                )
                .join(
                    ContentFact,
                    ContentFact.content_hash == Document.content_hash,
                )
                .join(File, File.document_id == Document.id)
                .where(
                    Document.id != document.id,
                    File.source_id == source_id,
                    ContentFact.fingerprint.isnot(None),
                    File.path.like(prefix + "%"),
                )
            )
        ).all()

        target_stem = stem_key(filename)
        best: tuple[float, int, int, int | None] | None = None
        for cand_id, cand_cluster, cand_fp, cand_path, cand_name in rows:
            if os.path.dirname(cand_path) != parent_dir:
                continue
            if stem_key(cand_name) != target_stem:
                continue
            similarity = fingerprint_similarity(fingerprint, cand_fp)
            if similarity < threshold:
                continue
            cluster_key = cand_cluster if cand_cluster is not None else cand_id
            if best is None or similarity > best[0]:
                best = (similarity, cluster_key, cand_id, cand_cluster)

        if best is None:
            return None

        _, cluster_key, cand_id, cand_cluster = best
        document.cluster_id = cluster_key
        if cand_cluster is None:
            candidate = await session.get(Document, cand_id)
            if candidate is not None and candidate.cluster_id is None:
                candidate.cluster_id = cluster_key
        await session.flush()
        return cluster_key

    @staticmethod
    async def list_cluster(
        session: AsyncSession, cluster_id: int
    ) -> list[tuple[Document, File]]:
        """Representative (document, newest file) pairs in a cluster."""
        rows = (
            await session.execute(
                select(Document, File)
                .join(File, File.document_id == Document.id)
                .where(Document.cluster_id == cluster_id)
                .order_by(File.file_modified_at.desc(), File.id)
            )
        ).all()

        seen: set[int] = set()
        result: list[tuple[Document, File]] = []
        for document, file in rows:
            if document.id in seen:
                continue
            seen.add(document.id)
            result.append((document, file))
        return result

    @staticmethod
    async def set_status(
        session: AsyncSession, document: Document, status: str
    ) -> None:
        """Set a document's status and mirror it to live instances.

        ``missing`` (path gone) and ``deferred`` (cloud placeholder awaiting
        hydration) are instance-only states and keep their value.
        """
        document.status = status
        instances = (
            await session.execute(
                select(File).where(File.document_id == document.id)
            )
        ).scalars().all()
        for inst in instances:
            if inst.status not in ("missing", "deferred"):
                inst.status = status


# ---------------------------------------------------------------------------
# ContentFactRepository (Phase 2a, design §5)
# ---------------------------------------------------------------------------


class ContentFactRepository:
    """Derived facts keyed by document content hash."""

    @staticmethod
    async def get(session: AsyncSession, content_hash: str) -> ContentFact | None:
        """Return the fact row for *content_hash* (legacy bare hex accepted)."""
        return await session.get(ContentFact, prefixed_hash(content_hash))

    @staticmethod
    def is_fresh(
        fact: ContentFact | None,
        *,
        extractor_version: str,
        facts_version: int,
    ) -> bool:
        """A row is reusable only when both versions still match (design §5.2)."""
        return (
            fact is not None
            and fact.extractor_version == extractor_version
            and fact.facts_version == facts_version
        )

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        content_hash: str,
        extractor_version: str,
        facts_version: int,
        summary: str | None = None,
        doc_type: str | None = None,
        language: str | None = None,
        entities: dict | None = None,
        topics: list | None = None,
        provenance: dict | None = None,
        fingerprint: str | None = None,
        char_count: int | None = None,
        page_count: int | None = None,
    ) -> ContentFact:
        """Create or replace the fact row for *content_hash*."""
        key = prefixed_hash(content_hash)
        fact = await session.get(ContentFact, key)
        if fact is None:
            fact = ContentFact(content_hash=key)
            session.add(fact)
        fact.extractor_version = extractor_version
        fact.facts_version = facts_version
        fact.summary = summary
        fact.doc_type = doc_type
        fact.language = language
        fact.entities = entities
        fact.topics = topics
        fact.provenance = provenance
        fact.fingerprint = fingerprint
        fact.char_count = char_count
        fact.page_count = page_count
        # Summary/topics may have changed, so any previous vector is stale.
        fact.embedding = None
        fact.embedding_model = None
        fact.embedding_dim = None
        await session.flush()
        return fact

    @staticmethod
    def is_embedding_fresh(
        fact: ContentFact | None, *, model: str, dim: int | None = None
    ) -> bool:
        """A vector is reusable only for the same model (and dim, if known)."""
        if fact is None or not fact.embedding or fact.embedding_model != model:
            return False
        return dim is None or fact.embedding_dim == dim

    @staticmethod
    async def update_embedding(
        session: AsyncSession,
        *,
        content_hash: str,
        embedding: bytes,
        model: str,
        dim: int,
    ) -> ContentFact | None:
        """Attach/replace a document's vector without touching its facts."""
        fact = await session.get(ContentFact, prefixed_hash(content_hash))
        if fact is None:
            return None
        fact.embedding = embedding
        fact.embedding_model = model
        fact.embedding_dim = dim
        await session.flush()
        return fact

    @staticmethod
    async def purge_unreferenced(session: AsyncSession) -> int:
        """Delete facts no facts-enabled source still references (D3).

        Facts are shared per hash, so turning Tier 1 off for one source must
        keep rows still reachable through another enabled source.
        """
        referenced = (
            select(Document.content_hash)
            .join(File, File.document_id == Document.id)
            .join(Source, Source.id == File.source_id)
            .where(
                Source.store_facts == True,  # noqa: E712
                Document.content_hash.isnot(None),
            )
        )
        result = await session.execute(
            delete(ContentFact).where(ContentFact.content_hash.notin_(referenced))
        )
        await session.flush()
        return result.rowcount or 0


# ---------------------------------------------------------------------------
# TextIndexRepository (Phase 2b Tier 2, design §5.3/§5.4)
# ---------------------------------------------------------------------------

_TEXT_INDEX_TABLE = "content_text_fts"


class TextIndexRepository:
    """Opt-in FTS5 index over extracted text, keyed by content hash.

    Raw text is normally ephemeral; this table exists only for sources with
    ``store_text_index`` enabled and is purged when that flag is turned off.
    Every method tolerates an SQLite build without FTS5 by degrading to a
    no-op (search still works over filename/facts).
    """

    @staticmethod
    async def _ensure(session: AsyncSession) -> bool:
        try:
            await session.execute(
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_TEXT_INDEX_TABLE} "
                    "USING fts5(content_hash UNINDEXED, text)"
                )
            )
            return True
        except Exception:
            logger.warning("FTS5 unavailable; skipping Tier-2 text index")
            return False

    @staticmethod
    async def has(session: AsyncSession, content_hash: str) -> bool:
        if not await TextIndexRepository._ensure(session):
            return False
        result = await session.execute(
            text(
                f"SELECT 1 FROM {_TEXT_INDEX_TABLE} "
                "WHERE content_hash = :h LIMIT 1"
            ),
            {"h": prefixed_hash(content_hash)},
        )
        return result.first() is not None

    @staticmethod
    async def upsert(session: AsyncSession, content_hash: str, text_value: str) -> None:
        if not text_value:
            return
        if not await TextIndexRepository._ensure(session):
            return
        key = prefixed_hash(content_hash)
        await session.execute(
            text(f"DELETE FROM {_TEXT_INDEX_TABLE} WHERE content_hash = :h"),
            {"h": key},
        )
        await session.execute(
            text(
                f"INSERT INTO {_TEXT_INDEX_TABLE} (content_hash, text) "
                "VALUES (:h, :t)"
            ),
            {"h": key, "t": text_value},
        )

    @staticmethod
    async def delete(session: AsyncSession, content_hash: str) -> None:
        if not await TextIndexRepository._ensure(session):
            return
        await session.execute(
            text(f"DELETE FROM {_TEXT_INDEX_TABLE} WHERE content_hash = :h"),
            {"h": prefixed_hash(content_hash)},
        )

    @staticmethod
    async def purge_unreferenced(session: AsyncSession) -> int:
        """Drop index rows no text-index-enabled source still references."""
        if not await TextIndexRepository._ensure(session):
            return 0
        result = await session.execute(
            text(
                f"DELETE FROM {_TEXT_INDEX_TABLE} WHERE content_hash NOT IN ("
                "SELECT d.content_hash FROM documents d"
                " JOIN files f ON f.document_id = d.id"
                " JOIN sources s ON s.id = f.source_id"
                " WHERE s.store_text_index = 1"
                " AND d.content_hash IS NOT NULL)"
            )
        )
        return result.rowcount or 0

    @staticmethod
    async def search_hashes(session: AsyncSession, query: str) -> list[str]:
        """Content hashes whose indexed text matches *query* (up to 500)."""
        fts_query = _fts_query(query)
        if fts_query is None:
            return []
        if not await TextIndexRepository._ensure(session):
            return []
        try:
            result = await session.execute(
                text(
                    f"SELECT content_hash FROM {_TEXT_INDEX_TABLE} "
                    f"WHERE {_TEXT_INDEX_TABLE} MATCH :q LIMIT 500"
                ),
                {"q": fts_query},
            )
        except Exception:
            logger.warning("FTS query failed for %r", query, exc_info=True)
            return []
        return [row[0] for row in result.all()]


def _fts_query(query: str) -> str | None:
    """Build a safe FTS5 query from user input (terms joined with AND)."""
    terms = [t for t in re.findall(r"\w+", query, flags=re.UNICODE) if len(t) >= 2]
    if not terms:
        return None
    return " AND ".join(f'"{term}"' for term in terms[:8])


# ---------------------------------------------------------------------------
# FileRepository
# ---------------------------------------------------------------------------


class FileRepository:
    """CRUD and query operations for :class:`File` rows."""

    @staticmethod
    async def list_files(
        session: AsyncSession,
        *,
        category_id: int | None = None,
        status: str | None = None,
        extension: str | None = None,
        source_id: int | None = None,
        query: str | None = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[File], int]:
        """Return a paginated, filtered file list together with the total count.

        Parameters
        ----------
        category_id:
            If provided, only return files that belong to this category.
        status / extension / source_id:
            Optional equality filters.
        query:
            Server-side text search over filename, path, and cached facts
            (summary/doc type/topics). ``LIKE`` is sufficient at this scale
            (design §5.3).
        sort_by:
            Column name to sort by (default ``created_at``).
        sort_dir:
            ``'asc'`` or ``'desc'`` (default ``'desc'``).
        offset / limit:
            Pagination controls.

        Returns
        -------
        tuple
            ``(rows, total_count)``
        """
        base = select(File).options(
            selectinload(File.categories).joinedload(FileCategory.category),
            selectinload(File.document)
            .selectinload(Document.categories)
            .joinedload(DocumentCategory.category),
        )
        count_base = select(func.count(File.id))

        if category_id is not None:
            # Effective membership: document-level assignment or a
            # per-instance addition (EXISTS avoids duplicate rows).
            in_category = or_(
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
            base = base.where(in_category)
            count_base = count_base.where(in_category)
        if status is not None:
            base = base.where(File.status == status)
            count_base = count_base.where(File.status == status)
        if extension is not None:
            base = base.where(File.extension == extension)
            count_base = count_base.where(File.extension == extension)
        if source_id is not None:
            base = base.where(File.source_id == source_id)
            count_base = count_base.where(File.source_id == source_id)

        if query is not None and (needle := query.strip()):
            pattern = f"%{needle}%"
            # Facts are per document hash, so join through the instance's
            # document. Exact duplicates share the row.
            base = base.outerjoin(
                Document, File.document_id == Document.id
            ).outerjoin(
                ContentFact, ContentFact.content_hash == Document.content_hash
            )
            count_base = count_base.outerjoin(
                Document, File.document_id == Document.id
            ).outerjoin(
                ContentFact, ContentFact.content_hash == Document.content_hash
            )
            conditions = [
                File.filename.ilike(pattern),
                File.path.ilike(pattern),
                ContentFact.summary.ilike(pattern),
                ContentFact.doc_type.ilike(pattern),
                cast(ContentFact.topics, String).ilike(pattern),
            ]
            # Tier 2 is opt-in per source; the FTS table only contains rows
            # for those sources, so matching hashes is naturally scoped.
            fts_hashes = await TextIndexRepository.search_hashes(session, needle)
            if fts_hashes:
                conditions.append(Document.content_hash.in_(fts_hashes))
            matches = or_(*conditions)
            base = base.where(matches)
            count_base = count_base.where(matches)

        # Sorting -- allowlisted so arbitrary model attributes (relationships,
        # methods) can't be resolved into the query.
        if sort_by not in _FILE_SORT_COLUMNS:
            sort_by = "created_at"
        sort_column = getattr(File, sort_by)
        order = sort_column.desc() if sort_dir == "desc" else sort_column.asc()
        base = base.order_by(order).offset(offset).limit(limit)

        total_result = await session.execute(count_base)
        total: int = total_result.scalar_one()

        rows_result = await session.execute(base)
        rows = rows_result.scalars().all()

        return rows, total

    @staticmethod
    async def get_by_id(session: AsyncSession, file_id: int) -> File | None:
        """Return a single file with its categories eagerly loaded."""
        stmt = (
            select(File)
            .where(File.id == file_id)
            .options(
                selectinload(File.categories).joinedload(FileCategory.category),
                selectinload(File.document)
                .selectinload(Document.categories)
                .joinedload(DocumentCategory.category),
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_path(session: AsyncSession, path: str) -> File | None:
        result = await session.execute(select(File).where(File.path == path))
        return result.scalar_one_or_none()

    @staticmethod
    async def create_bulk(session: AsyncSession, files: list[dict[str, Any]]) -> list[File]:
        """Insert multiple files at once.

        Each dict in *files* must contain at least the keys required by the
        ``File`` model (``source_id``, ``path``, ``filename``, ``size_bytes``).
        """
        instances = [File(**data) for data in files]
        session.add_all(instances)
        await session.flush()
        return instances

    @staticmethod
    async def update_status(
        session: AsyncSession, file_id: int, status: str
    ) -> File | None:
        file = await session.get(File, file_id)
        if file is None:
            return None
        file.status = status
        file.updated_at = _utcnow()
        await session.flush()
        return file

    @staticmethod
    async def get_pending_files(
        session: AsyncSession, batch_size: int = 50
    ) -> Sequence[File]:
        """Return the next batch of files with ``status='pending'``."""
        stmt = (
            select(File)
            .where(File.status == "pending")
            .order_by(File.created_at.asc())
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def mark_missing(session: AsyncSession, paths: list[str]) -> int:
        """Mark all files whose path is in *paths* as ``'missing'``.

        Returns the number of rows updated.
        """
        if not paths:
            return 0
        stmt = (
            update(File)
            .where(File.path.in_(paths))
            .values(status="missing", updated_at=_utcnow())
        )
        result = await session.execute(stmt)
        await session.flush()
        return result.rowcount  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# CategoryRepository
# ---------------------------------------------------------------------------


class CategoryRepository:
    """CRUD and tree-query operations for :class:`Category` rows."""

    @staticmethod
    async def get_tree(session: AsyncSession) -> list[dict[str, Any]]:
        """Return all categories as a nested tree structure.

        Each node is a dict with the category's attributes plus a ``children``
        list containing its sub-categories (recursively).
        """
        result = await session.execute(
            select(Category)
            .options(
                selectinload(Category.aliases),
                selectinload(Category.examples),
            )
            .order_by(Category.name)
        )
        all_cats: Sequence[Category] = result.scalars().all()

        # Build lookup and assemble tree in-memory.
        lookup: dict[int, dict[str, Any]] = {}
        for cat in all_cats:
            lookup[cat.id] = {
                "id": cat.id,
                "name": cat.name,
                "slug": cat.slug,
                "parent_id": cat.parent_id,
                "definition": cat.definition,
                "status": cat.status,
                "origin": cat.origin,
                "merged_into_id": cat.merged_into_id,
                "aliases": [alias.alias for alias in cat.aliases],
                "example_count": len(cat.examples),
                "description": cat.description,
                "color": cat.color,
                "is_ai_suggested": cat.is_ai_suggested,
                "created_at": cat.created_at,
                "children": [],
            }

        roots: list[dict[str, Any]] = []
        for node in lookup.values():
            parent_id = node["parent_id"]
            if parent_id is not None and parent_id in lookup:
                lookup[parent_id]["children"].append(node)
            else:
                roots.append(node)

        return roots

    @staticmethod
    async def get_flat_with_counts(session: AsyncSession) -> Sequence[dict[str, Any]]:
        """Return all categories with a ``file_count`` annotation.

        Counts *documents* (design §3.1): duplicates of one document count
        once. A per-instance addition counts its file's document, or the
        file itself (negative key) while it has no document yet.
        """
        cats = (
            await session.execute(
                select(Category)
                .options(
                    selectinload(Category.aliases),
                    selectinload(Category.examples),
                )
                .order_by(Category.name)
            )
        ).scalars().all()

        keys: dict[int, set[int]] = {cat.id: set() for cat in cats}
        doc_rows = (
            await session.execute(
                select(DocumentCategory.category_id, DocumentCategory.document_id)
            )
        ).all()
        for category_id, document_id in doc_rows:
            if category_id in keys:
                keys[category_id].add(document_id)
        inst_rows = (
            await session.execute(
                select(FileCategory.category_id, File.document_id, File.id)
                .join(File, File.id == FileCategory.file_id)
            )
        ).all()
        for category_id, document_id, file_id in inst_rows:
            if category_id in keys:
                keys[category_id].add(
                    document_id if document_id is not None else -file_id
                )

        return [
            {"category": cat, "file_count": len(keys[cat.id])} for cat in cats
        ]

    @staticmethod
    async def get_by_id(session: AsyncSession, category_id: int) -> Category | None:
        return await session.get(Category, category_id)

    @staticmethod
    async def get_with_relations(
        session: AsyncSession, category_id: int
    ) -> Category | None:
        result = await session.execute(
            select(Category)
            .options(
                selectinload(Category.aliases),
                selectinload(Category.examples),
            )
            .where(Category.id == category_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_document_count(
        session: AsyncSession, category_id: int
    ) -> int:
        """Distinct documents assigned to a concept (either level)."""
        keys: set[int] = set()
        doc_rows = (
            await session.execute(
                select(DocumentCategory.document_id).where(
                    DocumentCategory.category_id == category_id
                )
            )
        ).scalars().all()
        keys.update(doc_rows)
        inst_rows = (
            await session.execute(
                select(File.document_id, File.id)
                .join(FileCategory, File.id == FileCategory.file_id)
                .where(FileCategory.category_id == category_id)
            )
        ).all()
        for document_id, file_id in inst_rows:
            keys.add(document_id if document_id is not None else -file_id)
        return len(keys)

    @staticmethod
    async def get_sample_files(
        session: AsyncSession, category_id: int, limit: int = 3
    ) -> list[tuple[int, str, str | None]]:
        """A few (file_id, filename, content_hash) examples for review."""
        rows = list(
            (
                await session.execute(
                    select(File.id, File.filename, File.content_hash)
                    .join(
                        DocumentCategory,
                        DocumentCategory.document_id == File.document_id,
                    )
                    .where(DocumentCategory.category_id == category_id)
                    .order_by(File.id)
                    .limit(limit)
                )
            ).all()
        )
        if len(rows) < limit:
            seen = {row[0] for row in rows}
            instance_rows = (
                await session.execute(
                    select(File.id, File.filename, File.content_hash)
                    .join(FileCategory, FileCategory.file_id == File.id)
                    .where(FileCategory.category_id == category_id)
                    .order_by(File.id)
                    .limit(limit)
                )
            ).all()
            for row in instance_rows:
                if row[0] in seen:
                    continue
                rows.append(row)
                seen.add(row[0])
                if len(rows) >= limit:
                    break
        return [(row[0], row[1], row[2]) for row in rows[:limit]]

    @staticmethod
    async def promote_if_ready(
        session: AsyncSession, category_id: int, threshold: int = 3
    ) -> bool:
        """Approve a provisional concept once it covers enough documents."""
        category = await session.get(Category, category_id)
        if category is None or category.status != "provisional":
            return False
        count = await CategoryRepository.get_document_count(session, category_id)
        if count < threshold:
            return False
        category.status = "approved"
        await session.flush()
        return True

    @staticmethod
    async def get_by_slug(session: AsyncSession, slug: str) -> Category | None:
        result = await session.execute(select(Category).where(Category.slug == slug))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> Sequence[Category]:
        """All concepts with aliases/examples eager-loaded (Phase 3)."""
        result = await session.execute(
            select(Category)
            .options(
                selectinload(Category.aliases),
                selectinload(Category.examples),
            )
            .order_by(Category.name)
        )
        return result.scalars().all()

    @staticmethod
    async def get_by_normalized_alias(
        session: AsyncSession, normalized: str
    ) -> Category | None:
        result = await session.execute(
            select(Category)
            .join(CategoryAlias, CategoryAlias.category_id == Category.id)
            .options(selectinload(Category.aliases))
            .where(CategoryAlias.normalized == normalized)
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def add_alias(
        session: AsyncSession,
        category_id: int,
        alias: str,
        *,
        source: str = "ai",
    ) -> CategoryAlias | None:
        """Attach a synonym, enforcing global uniqueness (D9).

        Raises:
            ValueError: when the normalized alias belongs to another concept
                or shadows another concept's canonical name.
        """
        normalized = normalize_concept(alias)
        if not normalized:
            return None
        category = await session.get(Category, category_id)
        if category is None:
            return None

        existing = (
            await session.execute(
                select(CategoryAlias).where(
                    CategoryAlias.normalized == normalized
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.category_id == category_id:
                return existing
            raise ValueError(
                f"Alias {alias!r} already refers to another category"
            )

        # Canonical names reserve their normalized form too.
        categories = (await session.execute(select(Category))).scalars().all()
        for other in categories:
            if other.id != category_id and normalize_concept(other.name) == normalized:
                raise ValueError(
                    f"Alias {alias!r} conflicts with category {other.name!r}"
                )

        row = CategoryAlias(
            category_id=category_id,
            alias=alias.strip(),
            normalized=normalized,
            source=source,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def delete_alias(
        session: AsyncSession, category_id: int, alias_id: int
    ) -> bool:
        alias = await session.get(CategoryAlias, alias_id)
        if alias is None or alias.category_id != category_id:
            return False
        await session.delete(alias)
        await session.flush()
        return True

    @staticmethod
    async def add_example(
        session: AsyncSession,
        category_id: int,
        *,
        filename: str,
        content_hash: str | None = None,
        summary_snippet: str | None = None,
        source: str = "ai",
    ) -> CategoryExample | None:
        """Record a document as an example of *category_id* (learning loop)."""
        if await session.get(Category, category_id) is None:
            return None
        if content_hash is not None:
            existing = (
                await session.execute(
                    select(CategoryExample).where(
                        CategoryExample.category_id == category_id,
                        CategoryExample.content_hash == content_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
        example = CategoryExample(
            category_id=category_id,
            content_hash=content_hash,
            filename=filename,
            summary_snippet=summary_snippet,
            source=source,
        )
        session.add(example)
        await session.flush()
        return example

    @staticmethod
    async def set_embedding(
        session: AsyncSession,
        category_id: int,
        *,
        embedding: bytes,
        model: str,
        dim: int,
    ) -> Category | None:
        category = await session.get(Category, category_id)
        if category is None:
            return None
        category.embedding = embedding
        category.embedding_model = model
        category.embedding_dim = dim
        await session.flush()
        return category

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        slug: str | None = None,
        parent_id: int | None = None,
        description: str | None = None,
        color: str | None = None,
        is_ai_suggested: bool = False,
        definition: str | None = None,
        status: str = "approved",
        origin: str = "manual",
    ) -> Category:
        if slug is None:
            slug = _slugify(name)
        category = Category(
            name=name,
            slug=slug,
            parent_id=parent_id,
            description=description,
            color=color,
            is_ai_suggested=is_ai_suggested,
            definition=definition,
            status=status,
            origin=origin,
        )
        session.add(category)
        await session.flush()
        return category

    @staticmethod
    async def update(
        session: AsyncSession,
        category_id: int,
        **kwargs: Any,
    ) -> Category | None:
        category = await session.get(Category, category_id)
        if category is None:
            return None

        new_parent_id = kwargs.get("parent_id")
        if new_parent_id is not None:
            await CategoryRepository._ensure_no_cycle(
                session, category_id, new_parent_id
            )

        for key, value in kwargs.items():
            if hasattr(category, key):
                setattr(category, key, value)
        await session.flush()
        return category

    @staticmethod
    async def _ensure_no_cycle(
        session: AsyncSession, category_id: int, new_parent_id: int
    ) -> None:
        """Raise ``ValueError`` if parenting *category_id* under
        *new_parent_id* would create a cycle (including self-parenting)."""
        current_id: int | None = new_parent_id
        seen: set[int] = set()
        while current_id is not None and current_id not in seen:
            if current_id == category_id:
                raise ValueError(
                    "Cannot set parent: it would create a category cycle"
                )
            seen.add(current_id)
            parent = await session.get(Category, current_id)
            if parent is None:
                raise ValueError(f"Parent category {current_id} not found")
            current_id = parent.parent_id

    @staticmethod
    async def delete(session: AsyncSession, category_id: int) -> bool:
        """Delete a category, reassigning its children to its parent.

        Returns ``True`` if the category existed and was removed.
        """
        category = await session.get(Category, category_id)
        if category is None:
            return False

        # Re-parent children to the deleted category's parent.
        await session.execute(
            update(Category)
            .where(Category.parent_id == category_id)
            .values(parent_id=category.parent_id)
        )

        await session.delete(category)
        await session.flush()
        return True

    @staticmethod
    async def get_or_create_from_path(
        session: AsyncSession,
        path: str,
        *,
        is_ai_suggested: bool = False,
    ) -> Category:
        """Resolve a ``"Parent > Child > Grandchild"`` path to a category.

        Intermediate categories are created as needed.  Returns the leaf
        category.
        """
        parts = [p.strip() for p in path.split(">") if p.strip()]
        if not parts:
            raise ValueError("Category path must not be empty")

        parent_id: int | None = None
        current: Category | None = None

        for part in parts:
            slug = _slugify(part)
            stmt = select(Category).where(
                Category.slug == slug,
                Category.parent_id == parent_id if parent_id is not None
                else Category.parent_id.is_(None),
            )
            result = await session.execute(stmt)
            current = result.scalar_one_or_none()

            if current is None:
                current = Category(
                    name=part,
                    slug=slug,
                    parent_id=parent_id,
                    is_ai_suggested=is_ai_suggested,
                )
                session.add(current)
                await session.flush()

            parent_id = current.id

        assert current is not None
        return current

    @staticmethod
    async def get_files(
        session: AsyncSession,
        category_id: int,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[File], int]:
        """Return paginated files belonging to *category_id*."""
        in_category = or_(
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
        base = select(File).where(in_category)
        count_stmt = select(func.count(File.id)).where(in_category)

        total_result = await session.execute(count_stmt)
        total: int = total_result.scalar_one()

        rows_result = await session.execute(
            base.order_by(File.filename.asc()).offset(offset).limit(limit)
        )
        rows = rows_result.scalars().all()

        return rows, total


# ---------------------------------------------------------------------------
# JobRepository
# ---------------------------------------------------------------------------


class JobRepository:
    """CRUD operations for background :class:`Job` rows."""

    @staticmethod
    async def list_recent(
        session: AsyncSession, limit: int = 20
    ) -> Sequence[Job]:
        stmt = (
            select(Job)
            .options(joinedload(Job.source))
            .order_by(Job.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return result.scalars().unique().all()

    @staticmethod
    async def get_by_id(session: AsyncSession, job_id: int) -> Job | None:
        stmt = select(Job).where(Job.id == job_id).options(joinedload(Job.source))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        job_type: str,
        source_id: int | None = None,
        total_files: int = 0,
        trigger: str = "manual",
    ) -> Job:
        job = Job(
            job_type=job_type,
            source_id=source_id,
            total_files=total_files,
            trigger=trigger,
        )
        session.add(job)
        await session.flush()
        return job

    @staticmethod
    async def update_progress(
        session: AsyncSession,
        job_id: int,
        *,
        processed_files: int | None = None,
        error_count: int | None = None,
        error_log: str | None = None,
        status: str | None = None,
        total_files: int | None = None,
    ) -> Job | None:
        job = await session.get(Job, job_id)
        if job is None:
            return None
        if processed_files is not None:
            job.processed_files = processed_files
        if error_count is not None:
            job.error_count = error_count
        if error_log is not None:
            job.error_log = error_log
        if status is not None:
            job.status = status
        if total_files is not None:
            job.total_files = total_files
        job.updated_at = _utcnow()
        await session.flush()
        return job

    @staticmethod
    async def fail_stale_jobs(session: AsyncSession) -> int:
        """Mark jobs left ``'running'``/``'pending'`` by a dead process as failed.

        Jobs run as in-process background tasks, so at startup no job can
        legitimately be active; anything non-terminal was interrupted by a
        crash or quit and would otherwise show as syncing forever.
        """
        stmt = (
            update(Job)
            .where(Job.status.in_(("running", "pending")))
            .values(
                status="failed",
                error_log="interrupted: the app quit while this job was running",
                updated_at=_utcnow(),
            )
        )
        result = await session.execute(stmt)
        return result.rowcount or 0

    @staticmethod
    async def cancel(session: AsyncSession, job_id: int) -> Job | None:
        """Mark a job as ``'cancelled'``.

        Jobs already in a terminal state are returned unchanged.
        """
        job = await session.get(Job, job_id)
        if job is None:
            return None
        if job.status in ("completed", "completed_with_errors", "failed", "cancelled"):
            return job
        job.status = "cancelled"
        job.updated_at = _utcnow()
        await session.flush()
        return job


# ---------------------------------------------------------------------------
# LLMConfigRepository
# ---------------------------------------------------------------------------


class LLMConfigRepository:
    """CRUD operations for :class:`LLMConfig` rows."""

    @staticmethod
    async def get_active(
        session: AsyncSession, kind: str = "chat"
    ) -> LLMConfig | None:
        """Return the active config for *kind* (``chat`` or ``embedding``)."""
        result = await session.execute(
            select(LLMConfig)
            .where(LLMConfig.kind == kind, LLMConfig.is_active.is_(True))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> Sequence[LLMConfig]:
        result = await session.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def save(
        session: AsyncSession,
        *,
        provider: str,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        is_active: bool = False,
        config_id: int | None = None,
        kind: str = "chat",
    ) -> LLMConfig:
        """Create or update an LLM configuration.

        If *is_active* is ``True``, other active configs of the same *kind*
        are deactivated first (chat and embedding are independent). If
        *config_id* is provided, the existing row is updated; otherwise a
        new row is inserted.
        """
        if is_active:
            await session.execute(
                update(LLMConfig)
                .where(LLMConfig.kind == kind)
                .values(is_active=False)
            )

        if config_id is not None:
            config = await session.get(LLMConfig, config_id)
            if config is not None:
                config.kind = kind
                config.provider = provider
                config.model = model
                config.api_key = api_key
                config.base_url = base_url
                config.is_active = is_active
                await session.flush()
                return config

        config = LLMConfig(
            kind=kind,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            is_active=is_active,
        )
        session.add(config)
        await session.flush()
        return config

    @staticmethod
    async def delete(session: AsyncSession, config_id: int) -> bool:
        config = await session.get(LLMConfig, config_id)
        if config is None:
            return False
        await session.delete(config)
        await session.flush()
        return True
