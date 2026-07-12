"""Tests for file category-assignment endpoints."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Category, File, FileCategory, Source


async def _setup_categorized_file(session: AsyncSession) -> tuple[File, Category]:
    source = Source(path="/tmp/cabinet-test-src", recursive=True)
    session.add(source)
    await session.flush()
    file = File(
        source_id=source.id,
        path="/tmp/cabinet-test-src/a.txt",
        filename="a.txt",
        extension=".txt",
        size_bytes=1,
        status="categorized",
    )
    category = Category(name="Finance", slug="finance")
    session.add_all([file, category])
    await session.flush()
    session.add(FileCategory(file_id=file.id, category_id=category.id, is_manual=False))
    await session.commit()
    return file, category


async def test_assign_pins_existing_ai_category(client: AsyncClient, session: AsyncSession):
    """Re-asserting an AI-assigned category must flip it to manual.

    Only is_manual rows survive recategorization, so a plain "already
    assigned, skip" would let the next run drop a user-confirmed category.
    """
    file, category = await _setup_categorized_file(session)

    resp = await client.post(
        f"/api/v1/files/{file.id}/categories", json={"category_ids": [category.id]}
    )
    assert resp.status_code == 201

    fc = (
        await session.execute(select(FileCategory).where(FileCategory.file_id == file.id))
    ).scalar_one()
    await session.refresh(fc)
    assert fc.is_manual is True


async def test_assign_new_category_is_manual(client: AsyncClient, session: AsyncSession):
    file, _ = await _setup_categorized_file(session)
    other = Category(name="Reports", slug="reports")
    session.add(other)
    await session.commit()

    resp = await client.post(
        f"/api/v1/files/{file.id}/categories", json={"category_ids": [other.id]}
    )
    assert resp.status_code == 201
    names = {c["name"] for c in resp.json()["categories"]}
    assert names == {"Finance", "Reports"}

    fc = (
        await session.execute(
            select(FileCategory).where(
                FileCategory.file_id == file.id, FileCategory.category_id == other.id
            )
        )
    ).scalar_one()
    assert fc.is_manual is True
