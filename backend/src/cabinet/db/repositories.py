"""Data-access repositories for the file-sorter application.

Every public method receives an ``AsyncSession`` as its first argument so that
callers control transaction boundaries.  All queries use SQLAlchemy 2.0-style
``select()`` / ``insert()`` / ``update()`` / ``delete()`` expressions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from cabinet.db.models import (
    Category,
    File,
    FileCategory,
    Job,
    LLMConfig,
    Source,
)

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
    async def create(
        session: AsyncSession,
        *,
        path: str,
        label: str | None = None,
        recursive: bool = True,
        exclude_patterns: list[str] | None = None,
    ) -> Source:
        source = Source(
            path=path,
            label=label,
            recursive=recursive,
            exclude_patterns=exclude_patterns or None,
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
            selectinload(File.categories).joinedload(FileCategory.category)
        )
        count_base = select(func.count(File.id))

        if category_id is not None:
            base = base.join(FileCategory).where(FileCategory.category_id == category_id)
            count_base = count_base.join(FileCategory).where(
                FileCategory.category_id == category_id
            )
        if status is not None:
            base = base.where(File.status == status)
            count_base = count_base.where(File.status == status)
        if extension is not None:
            base = base.where(File.extension == extension)
            count_base = count_base.where(File.extension == extension)
        if source_id is not None:
            base = base.where(File.source_id == source_id)
            count_base = count_base.where(File.source_id == source_id)

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
            .options(selectinload(File.categories).joinedload(FileCategory.category))
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
            select(Category).order_by(Category.name)
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
        """Return all categories with a ``file_count`` annotation."""
        stmt = (
            select(
                Category,
                func.count(FileCategory.file_id).label("file_count"),
            )
            .outerjoin(FileCategory, FileCategory.category_id == Category.id)
            .group_by(Category.id)
            .order_by(Category.name)
        )
        result = await session.execute(stmt)
        rows = result.all()
        return [
            {
                "category": row[0],
                "file_count": row[1],
            }
            for row in rows
        ]

    @staticmethod
    async def get_by_id(session: AsyncSession, category_id: int) -> Category | None:
        return await session.get(Category, category_id)

    @staticmethod
    async def get_by_slug(session: AsyncSession, slug: str) -> Category | None:
        result = await session.execute(select(Category).where(Category.slug == slug))
        return result.scalar_one_or_none()

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
        base = (
            select(File)
            .join(FileCategory)
            .where(FileCategory.category_id == category_id)
        )
        count_stmt = (
            select(func.count(File.id))
            .join(FileCategory)
            .where(FileCategory.category_id == category_id)
        )

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
    ) -> Job:
        job = Job(
            job_type=job_type,
            source_id=source_id,
            total_files=total_files,
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
    async def get_active(session: AsyncSession) -> LLMConfig | None:
        """Return the currently active LLM configuration (if any)."""
        result = await session.execute(
            select(LLMConfig).where(LLMConfig.is_active.is_(True)).limit(1)
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
    ) -> LLMConfig:
        """Create or update an LLM configuration.

        If *is_active* is ``True``, all other configs are deactivated first.
        If *config_id* is provided, the existing row is updated; otherwise a
        new row is inserted.
        """
        if is_active:
            await session.execute(
                update(LLMConfig).values(is_active=False)
            )

        if config_id is not None:
            config = await session.get(LLMConfig, config_id)
            if config is not None:
                config.provider = provider
                config.model = model
                config.api_key = api_key
                config.base_url = base_url
                config.is_active = is_active
                await session.flush()
                return config

        config = LLMConfig(
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
