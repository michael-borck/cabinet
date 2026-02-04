"""Async SQLAlchemy engine setup for SQLite."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from file_sorter.db.models import Base

# ---------------------------------------------------------------------------
# Database path configuration
# ---------------------------------------------------------------------------
# Resolve the database file path from the DATABASE_URL environment variable.
# Falls back to a default location relative to the project root.
# Expected format: a plain file path (e.g. "/data/file_sorter.db") or an
# SQLAlchemy-style URL ("sqlite+aiosqlite:///...").  When a plain path is
# provided it is automatically wrapped in the async dialect prefix.
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "file_sorter.db"
)


def _resolve_database_url() -> str:
    """Build the async SQLAlchemy database URL from configuration."""
    raw: str = os.environ.get("FILE_SORTER_DB_PATH", _DEFAULT_DB_PATH)
    if raw.startswith("sqlite"):
        # Already a full URL; make sure it uses the async driver.
        return raw.replace("sqlite://", "sqlite+aiosqlite://", 1)
    # Plain file-system path – ensure parent directory exists.
    abs_path = os.path.abspath(raw)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    return f"sqlite+aiosqlite:///{abs_path}"


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

engine: AsyncEngine = create_async_engine(
    _resolve_database_url(),
    echo=False,
    # SQLite does not benefit from a connection pool larger than 1 for writes,
    # but async access may require several readers.
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    """Enable WAL mode and other performance pragmas on every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.close()


async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-style dependency that yields an ``AsyncSession``."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create all tables and run cleanup migrations.

    This function is idempotent and safe to call on every application startup.
    """
    async with engine.begin() as conn:
        # Create standard ORM tables.
        await conn.run_sync(Base.metadata.create_all)

        # Cleanup migration: drop legacy FTS5 artifacts and text columns.
        await _cleanup_legacy_fts(conn)


async def _cleanup_legacy_fts(conn) -> None:
    """Drop FTS5 table, triggers, and text columns from previous schema.

    Each statement is wrapped in try/except so that a fresh database
    (which never had these objects) doesn't raise errors.
    """
    stmts = [
        "DROP TRIGGER IF EXISTS files_ai",
        "DROP TRIGGER IF EXISTS files_ad",
        "DROP TRIGGER IF EXISTS files_au",
        "DROP TABLE IF EXISTS files_fts",
    ]
    for stmt in stmts:
        try:
            await conn.execute(text(stmt))
        except Exception:
            logger.debug("Cleanup statement skipped: %s", stmt)

    # Drop legacy columns (SQLite >= 3.35.0 supports ALTER TABLE DROP COLUMN).
    for col in ("extracted_text", "text_preview"):
        try:
            await conn.execute(text(f"ALTER TABLE files DROP COLUMN {col}"))
        except Exception:
            logger.debug("Column '%s' already removed or does not exist", col)
