"""SQLAlchemy ORM models for the file-sorter application."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    recursive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # User-defined glob excludes for the scanner; NULL means none. Built-in
    # default exclusions (node_modules, __pycache__, ...) live in the scanner.
    exclude_patterns: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    files: Mapped[list[File]] = relationship(
        "File", back_populates="source", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship("Job", back_populates="source")

    def __repr__(self) -> str:
        return f"<Source id={self.id} path={self.path!r}>"


class Document(Base):
    """A unit of content, identified by its hash (Phase 1a).

    Category assignments and (from Phase 2) derived facts live here, so a
    document is extracted and categorised once however many ``File``
    instances (locations) it has. ``content_hash`` is NULL while a document
    is *provisional* (created for a not-yet-hashed file); provisional
    documents merge when backfill hashing reveals identical content.
    SQLite unique indexes treat NULLs as distinct, so many provisional
    documents can coexist.
    """

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_content_hash", "content_hash", unique=True),
        Index("ix_documents_status", "status"),
        Index("ix_documents_cluster_id", "cluster_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stored algorithm-prefixed ("sha256:<hex>"); legacy bare hex on
    # ``files.content_hash`` is treated as sha256 when documents are created.
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # Near-duplicate/version grouping (design §3.4); unused until 2b/3.
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    instances: Mapped[list[File]] = relationship(
        "File", back_populates="document"
    )
    categories: Mapped[list[DocumentCategory]] = relationship(
        "DocumentCategory", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} hash={self.content_hash!r}>"


class File(Base):
    """One location (instance) of a :class:`Document` on disk.

    ``status`` is retained as a per-instance mirror of the document's
    pipeline status plus the instance-only ``missing`` state, so existing
    API consumers keep working; the scanner/pipeline keep the two in sync.
    ``content_hash`` mirrors the document hash for the same reason.
    """

    __tablename__ = "files"
    __table_args__ = (
        # Unique per source, not globally: overlapping sources may legitimately
        # index the same file, each under its own source.
        Index("ix_files_source_path", "source_id", "path", unique=True),
        Index("ix_files_source_id", "source_id"),
        Index("ix_files_status", "status"),
        Index("ix_files_extension", "extension"),
        Index("ix_files_content_hash", "content_hash"),
        Index("ix_files_document_id", "document_id"),
        # Rename lookup within a source (design §3.2 step 2).
        Index("ix_files_source_device_inode", "source_id", "device_id", "inode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    # st_dev / st_ino at last sighting, for zero-read rename detection.
    # NULL (or 0) when the filesystem doesn't provide trustworthy values.
    device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    extension: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    file_modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    source: Mapped[Source] = relationship("Source", back_populates="files")
    document: Mapped[Document | None] = relationship(
        "Document", back_populates="instances"
    )
    # Per-instance manual category additions only (design §3.1); the
    # document-level assignments live on DocumentCategory. Rows written
    # before Phase 1a (incl. AI ones) are moved by the engine migration.
    categories: Mapped[list[FileCategory]] = relationship(
        "FileCategory", back_populates="file", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<File id={self.id} filename={self.filename!r}>"


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        # Slugs are unique per parent, not globally: "Reports" may exist under
        # both "Finance" and "Projects". NULL parents are distinct in SQLite
        # unique indexes, so top-level dedup is enforced by the get-or-create
        # lookups, which always run in the same transaction.
        Index("ix_categories_parent_slug", "parent_id", "slug", unique=True),
        Index("ix_categories_parent_id", "parent_id"),
        Index("ix_categories_slug", "slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    is_ai_suggested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    parent: Mapped[Category | None] = relationship(
        "Category", remote_side=[id], back_populates="children"
    )
    children: Mapped[list[Category]] = relationship("Category", back_populates="parent")
    file_categories: Mapped[list[FileCategory]] = relationship(
        "FileCategory", back_populates="category", cascade="all, delete-orphan"
    )
    document_categories: Mapped[list[DocumentCategory]] = relationship(
        "DocumentCategory", back_populates="category", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} slug={self.slug!r}>"


class FileCategory(Base):
    __tablename__ = "file_categories"
    __table_args__ = (
        Index("ix_file_categories_category_id", "category_id"),
    )

    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    file: Mapped[File] = relationship("File", back_populates="categories")
    category: Mapped[Category] = relationship("Category", back_populates="file_categories")

    def __repr__(self) -> str:
        return f"<FileCategory file_id={self.file_id} category_id={self.category_id}>"


class DocumentCategory(Base):
    """Category assignment at document level — shared by all instances."""

    __tablename__ = "document_categories"
    __table_args__ = (
        Index("ix_document_categories_category_id", "category_id"),
    )

    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship("Document", back_populates="categories")
    category: Mapped[Category] = relationship(
        "Category", back_populates="document_categories"
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentCategory document_id={self.document_id} "
            f"category_id={self.category_id}>"
        )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String, nullable=False)  # 'quick_sync' or 'full_reindex'
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    source_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Files recognised as moves/renames (Phase 1a) — work *avoided*, shown
    # to the user so a restructure reads as "moved", not "reprocessed".
    moved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    source: Mapped[Source | None] = relationship("Source", back_populates="jobs")

    def __repr__(self) -> str:
        return f"<Job id={self.id} type={self.job_type!r} status={self.status!r}>"


class LLMConfig(Base):
    __tablename__ = "llm_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<LLMConfig id={self.id} provider={self.provider!r} model={self.model!r}>"
