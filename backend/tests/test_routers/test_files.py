"""Tests for file category-assignment endpoints."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Category, Document, DocumentCategory, File, Source


async def _setup_categorized_file(session: AsyncSession) -> tuple[File, Category]:
    source = Source(path="/tmp/cabinet-test-src", recursive=True)
    session.add(source)
    await session.flush()
    # Phase 1a: assignments live on the file's Document, shared by every
    # instance of the same content.
    document = Document(content_hash="sha256:abc", status="categorized")
    session.add(document)
    await session.flush()
    file = File(
        source_id=source.id,
        document_id=document.id,
        path="/tmp/cabinet-test-src/a.txt",
        filename="a.txt",
        extension=".txt",
        size_bytes=1,
        status="categorized",
    )
    category = Category(name="Finance", slug="finance")
    session.add_all([file, category])
    await session.flush()
    session.add(
        DocumentCategory(
            document_id=document.id, category_id=category.id, is_manual=False
        )
    )
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

    dc = (
        await session.execute(
            select(DocumentCategory).where(
                DocumentCategory.document_id == file.document_id
            )
        )
    ).scalar_one()
    await session.refresh(dc)
    assert dc.is_manual is True


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

    dc = (
        await session.execute(
            select(DocumentCategory).where(
                DocumentCategory.document_id == file.document_id,
                DocumentCategory.category_id == other.id,
            )
        )
    ).scalar_one()
    assert dc.is_manual is True
