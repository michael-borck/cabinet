"""Tests for the additive column migration in db.engine."""

from sqlalchemy.ext.asyncio import create_async_engine

from cabinet.db.engine import _add_missing_columns, _migrate_documents
from cabinet.db.models import Base


async def _column_names(conn, table: str) -> set[str]:
    return {row[1] for row in await conn.exec_driver_sql(f"PRAGMA table_info({table})")}


async def test_add_missing_columns_upgrades_legacy_sources_table():
    """A sources table from before exclude_patterns gets the column added."""
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE sources ("
                "id INTEGER PRIMARY KEY, path VARCHAR NOT NULL, label VARCHAR, "
                "recursive BOOLEAN NOT NULL, last_scanned_at DATETIME, "
                "created_at DATETIME NOT NULL)"
            )
            await conn.exec_driver_sql(
                "INSERT INTO sources (path, recursive, created_at) "
                "VALUES ('/tmp/x', 1, '2026-01-01')"
            )

            await _add_missing_columns(conn)
            assert "exclude_patterns" in await _column_names(conn, "sources")

            # Idempotent: a second run must not raise.
            await _add_missing_columns(conn)

            # Existing rows read back as NULL (treated as "no excludes").
            value = (
                await conn.exec_driver_sql("SELECT exclude_patterns FROM sources")
            ).scalar()
            assert value is None
    finally:
        await engine.dispose()


async def test_migrate_documents_moves_assignments_and_is_idempotent():
    """Phase 1a: legacy files gain documents; assignments move with them.

    A second run must be a no-op — in particular it must not clear
    legitimate per-instance rows written after the migration.
    """
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Legacy state: two instances of one bare-hash document, each
            # carrying the same category (one manual, one AI).
            await conn.exec_driver_sql(
                "INSERT INTO sources (id, path, recursive) VALUES (1, '/tmp/x', 1)"
            )
            await conn.exec_driver_sql(
                "INSERT INTO categories (id, name, slug, is_ai_suggested)"
                " VALUES (1, 'Finance', 'finance', 0)"
            )
            for fid, path, manual in ((1, "/tmp/x/a.txt", 0), (2, "/tmp/x/b.txt", 1)):
                await conn.exec_driver_sql(
                    "INSERT INTO files (id, source_id, path, filename, size_bytes,"
                    " status, content_hash) VALUES (?, 1, ?, ?, 1, 'categorized',"
                    " 'abc123')",
                    (fid, path, path.rsplit("/", 1)[1]),
                )
                await conn.exec_driver_sql(
                    "INSERT INTO file_categories (file_id, category_id, is_manual)"
                    " VALUES (?, 1, ?)",
                    (fid, manual),
                )

            await _migrate_documents(conn)

            docs = (
                await conn.exec_driver_sql(
                    "SELECT id, content_hash, status FROM documents"
                )
            ).all()
            assert len(docs) == 1
            assert docs[0][1] == "sha256:abc123"  # normalised to prefixed form
            assert docs[0][2] == "categorized"
            doc_id = docs[0][0]
            assigned = (
                await conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM files WHERE document_id = ?", (doc_id,)
                )
            ).scalar()
            assert assigned == 2
            moved = (
                await conn.exec_driver_sql(
                    "SELECT is_manual FROM document_categories"
                    " WHERE document_id = ? AND category_id = 1",
                    (doc_id,),
                )
            ).scalar()
            assert moved == 1  # manual beats AI on duplicate transfer
            leftovers = (
                await conn.exec_driver_sql("SELECT COUNT(*) FROM file_categories")
            ).scalar()
            assert leftovers == 0

            # A post-migration per-instance row survives a second run.
            await conn.exec_driver_sql(
                "INSERT INTO file_categories (file_id, category_id, is_manual)"
                " VALUES (1, 1, 1)"
            )
            await _migrate_documents(conn)
            kept = (
                await conn.exec_driver_sql("SELECT COUNT(*) FROM file_categories")
            ).scalar()
            assert kept == 1
    finally:
        await engine.dispose()
