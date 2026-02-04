"""SQLAlchemy ORM models for the file-sorter application."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
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


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_source_id", "source_id"),
        Index("ix_files_status", "status"),
        Index("ix_files_extension", "extension"),
        Index("ix_files_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
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
    categories: Mapped[list[FileCategory]] = relationship(
        "FileCategory", back_populates="file", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<File id={self.id} filename={self.filename!r}>"


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        Index("ix_categories_parent_id", "parent_id"),
        Index("ix_categories_slug", "slug", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
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
