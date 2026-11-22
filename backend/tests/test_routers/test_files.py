"""Tests for file category-assignment endpoints."""

from datetime import datetime

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import (
    Category,
    CategoryExample,
    Document,
    DocumentCategory,
    File,
    Source,
)


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


def test_availability_precedence():
    """missing > source_unavailable > cloud_placeholder > available."""
    cloud_file = File(
        source_id=1,
        path="/x",
        filename="x",
        size_bytes=1,
        status="deferred",
        is_placeholder=True,
    )
    assert cloud_file.availability_state() == "cloud_placeholder"
    assert cloud_file.availability_state(source_available=False) == "source_unavailable"

    missing = File(
        source_id=1,
        path="/x",
        filename="x",
        size_bytes=1,
        status="missing",
        is_placeholder=True,
    )
    assert missing.availability_state() == "missing"
    assert missing.availability_state(source_available=False) == "missing"


async def test_cloud_placeholder_reported_over_api(
    client: AsyncClient, session: AsyncSession, tmp_path
):
    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()
    file = File(
        source_id=source.id,
        path=str(tmp_path / "cloud.pdf"),
        filename="cloud.pdf",
        extension=".pdf",
        size_bytes=10,
        status="deferred",
        is_placeholder=True,
    )
    session.add(file)
    await session.commit()

    resp = await client.get(f"/api/v1/files/{file.id}")
    assert resp.status_code == 200
    assert resp.json()["availability"] == "cloud_placeholder"


async def test_get_file_facts(
    client: AsyncClient, session: AsyncSession, tmp_path
):
    from cabinet.db.repositories import ContentFactRepository

    source = Source(path=str(tmp_path), recursive=True, store_facts=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:factsx", status="categorized")
    session.add(document)
    await session.flush()
    file = File(
        source_id=source.id,
        document_id=document.id,
        content_hash=document.content_hash,
        path=str(tmp_path / "a.txt"),
        filename="a.txt",
        size_bytes=1,
        status="categorized",
    )
    session.add(file)
    await ContentFactRepository.upsert(
        session,
        content_hash="sha256:factsx",
        extractor_version="1",
        facts_version=2,
        summary="Invoice summary",
        doc_type="invoice",
        topics=["billing"],
    )
    await session.commit()

    response = await client.get(f"/api/v1/files/{file.id}/facts")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Invoice summary"
    assert body["doc_type"] == "invoice"
    assert body["topics"] == ["billing"]
    assert body["stale"] is False


async def test_get_file_facts_404_when_absent(client: AsyncClient, session: AsyncSession):
    source = Source(path="/tmp/cabinet-no-facts", recursive=True, store_facts=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:nofacts", status="pending")
    session.add(document)
    await session.flush()
    file = File(
        source_id=source.id,
        document_id=document.id,
        content_hash=document.content_hash,
        path="/tmp/cabinet-no-facts/a.txt",
        filename="a.txt",
        size_bytes=1,
        status="pending",
    )
    session.add(file)
    await session.commit()

    response = await client.get(f"/api/v1/files/{file.id}/facts")
    assert response.status_code == 404


async def test_list_files_q_searches_facts(
    client: AsyncClient, session: AsyncSession, tmp_path
):
    from cabinet.db.repositories import ContentFactRepository

    source = Source(path=str(tmp_path), recursive=True, store_facts=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:qsearch", status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        File(
            source_id=source.id,
            document_id=document.id,
            content_hash=document.content_hash,
            path=str(tmp_path / "a.txt"),
            filename="a.txt",
            size_bytes=1,
            status="categorized",
        )
    )
    await ContentFactRepository.upsert(
        session,
        content_hash="sha256:qsearch",
        extractor_version="1",
        facts_version=2,
        summary="A consulting invoice for Q1",
        doc_type="invoice",
        topics=["billing"],
    )
    await session.commit()

    response = await client.get("/api/v1/files", params={"q": "consulting"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["files"][0]["filename"] == "a.txt"


async def test_file_versions_endpoint(
    client: AsyncClient, session: AsyncSession, tmp_path
):
    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()

    primary = Document(content_hash="sha256:v1", status="categorized")
    session.add(primary)
    await session.flush()
    primary.cluster_id = primary.id
    other = Document(
        content_hash="sha256:v2", status="categorized", cluster_id=primary.id
    )
    session.add(other)
    await session.flush()

    primary_file = File(
        source_id=source.id,
        document_id=primary.id,
        content_hash=primary.content_hash,
        path=str(tmp_path / "report.txt"),
        filename="report.txt",
        size_bytes=1,
        status="categorized",
        file_modified_at=datetime(2026, 1, 1),
    )
    newer_file = File(
        source_id=source.id,
        document_id=other.id,
        content_hash=other.content_hash,
        path=str(tmp_path / "report_v2.txt"),
        filename="report_v2.txt",
        size_bytes=1,
        status="categorized",
        file_modified_at=datetime(2026, 1, 2),
    )
    session.add_all([primary_file, newer_file])
    await session.commit()

    response = await client.get(f"/api/v1/files/{primary_file.id}/versions")
    assert response.status_code == 200
    body = response.json()
    assert body["cluster_id"] == primary.id
    assert {v["filename"] for v in body["versions"]} == {
        "report.txt",
        "report_v2.txt",
    }
    marked = [v for v in body["versions"] if v["is_primary"]]
    assert len(marked) == 1
    assert marked[0]["filename"] == "report_v2.txt"  # newest by mtime


async def test_manual_assignment_records_example(
    client: AsyncClient, session: AsyncSession
):
    file, category = await _setup_categorized_file(session)

    response = await client.post(
        f"/api/v1/files/{file.id}/categories",
        json={"category_ids": [category.id]},
    )
    assert response.status_code == 201

    example = (
        await session.execute(
            select(CategoryExample).where(
                CategoryExample.category_id == category.id
            )
        )
    ).scalar_one_or_none()
    assert example is not None
    assert example.source == "manual"
    assert example.filename == "a.txt"
    assert example.content_hash == "sha256:abc"
