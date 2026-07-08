"""Tests for the additive column migration in db.engine."""

from sqlalchemy.ext.asyncio import create_async_engine

from cabinet.db.engine import _add_missing_columns


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
