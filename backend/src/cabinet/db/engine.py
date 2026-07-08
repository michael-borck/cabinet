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

from cabinet.db.models import Base

# ---------------------------------------------------------------------------
# Database path configuration
# ---------------------------------------------------------------------------
# Resolve the database file path from the DATABASE_URL environment variable.
# Falls back to a default location relative to the project root.
# Expected format: a plain file path (e.g. "/data/cabinet.db") or an
# SQLAlchemy-style URL ("sqlite+aiosqlite:///...").  When a plain path is
# provided it is automatically wrapped in the async dialect prefix.
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "cabinet.db"
)


def _resolve_database_url() -> str:
    """Build the async SQLAlchemy database URL from configuration."""
    raw: str = os.environ.get("CABINET_DB_PATH", _DEFAULT_DB_PATH)
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
    # Wait for a held write lock instead of failing immediately.
    cursor.execute("PRAGMA busy_timeout=5000;")
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
    # Tables created with legacy global-unique constraints must be rebuilt:
    # SQLite cannot drop a table-level UNIQUE without recreating the table.
    await _stash_legacy_tables()

    async with engine.begin() as conn:
        # Create standard ORM tables.
        await conn.run_sync(Base.metadata.create_all)

        # Cleanup migration: drop legacy FTS5 artifacts and text columns.
        await _cleanup_legacy_fts(conn)

        # Additive migration: columns introduced after the initial schema
        # (create_all never ALTERs existing tables).
        await _add_missing_columns(conn)

    await _restore_stashed_tables()


# Tables whose old schema carried a global UNIQUE that the current models
# replace with a composite unique index. The marker string identifies the
# legacy DDL in sqlite_master.
_LEGACY_UNIQUE_MARKERS = {
    "files": "UNIQUE (path)",
    "categories": "UNIQUE (slug)",
}

_MIGRATION_SUFFIX = "_migration_tmp"


async def _stash_legacy_tables() -> None:
    """Copy legacy-constrained tables aside and drop them.

    ``create_all`` then recreates them with the current schema, and
    :func:`_restore_stashed_tables` copies the rows back. Interrupted runs are
    safe: a leftover stash is either re-created (source table still present)
    or picked up by the restore step (source table already dropped).
    """
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for name, marker in _LEGACY_UNIQUE_MARKERS.items():
            tmp = f"{name}{_MIGRATION_SUFFIX}"
            ddl = (
                await conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
                    (name,),
                )
            ).scalar()
            has_tmp = (
                await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                    (tmp,),
                )
            ).scalar()

            if ddl is None or marker not in ddl:
                continue  # fresh DB or already migrated (tmp handled by restore)

            logger.info("Migrating table %r to per-scope unique constraints", name)
            try:
                await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                if has_tmp:
                    # Crashed mid-stash on a previous run; the source table
                    # still exists, so the stash can be rebuilt from scratch.
                    await conn.exec_driver_sql(f"DROP TABLE {tmp}")
                await conn.exec_driver_sql(
                    f"CREATE TABLE {tmp} AS SELECT * FROM {name}"
                )
                await conn.exec_driver_sql(f"DROP TABLE {name}")
            finally:
                await conn.exec_driver_sql("PRAGMA foreign_keys=ON")


async def _restore_stashed_tables() -> None:
    """Copy rows from ``*_migration_tmp`` stashes back into the new tables."""
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        tmp_names = (
            (
                await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    f"AND name LIKE '%{_MIGRATION_SUFFIX}'"
                )
            )
            .scalars()
            .all()
        )
        for tmp in tmp_names:
            target = tmp[: -len(_MIGRATION_SUFFIX)]
            target_cols = [
                row[1]
                for row in await conn.exec_driver_sql(f"PRAGMA table_info({target})")
            ]
            tmp_cols = {
                row[1]
                for row in await conn.exec_driver_sql(f"PRAGMA table_info({tmp})")
            }
            # Intersect so legacy columns (e.g. extracted_text) are dropped.
            cols = ", ".join(c for c in target_cols if c in tmp_cols)
            try:
                await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                await conn.exec_driver_sql(
                    f"INSERT OR IGNORE INTO {target} ({cols}) SELECT {cols} FROM {tmp}"
                )
                await conn.exec_driver_sql(f"DROP TABLE {tmp}")
            finally:
                await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            logger.info("Restored %r rows into migrated table", target)


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


# Columns added after the initial release: (table, column, DDL type).
_ADDED_COLUMNS = [
    ("sources", "exclude_patterns", "JSON"),
]


async def _add_missing_columns(conn) -> None:
    """ALTER TABLE to add columns that create_all won't add to existing tables."""
    for table, column, ddl_type in _ADDED_COLUMNS:
        existing = {
            row[1] for row in await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            logger.info("Added column %s.%s", table, column)
