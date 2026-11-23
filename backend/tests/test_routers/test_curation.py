"""Phase 5a curation export/import endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, DocumentCategory
from cabinet.db.repositories import CategoryRepository


async def test_curation_export_and_import(
    client: AsyncClient, session: AsyncSession
):
    category = await CategoryRepository.create(session, name="Finance")
    document = Document(content_hash="sha256:c1", status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        DocumentCategory(
            document_id=document.id,
            category_id=category.id,
            is_manual=True,
        )
    )
    await session.commit()

    exported = await client.get("/api/v1/curation/export")
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["version"] == 1
    assert payload["assignments"] == [
        {"content_hash": "sha256:c1", "concept_path": "Finance"}
    ]

    imported = await client.post("/api/v1/curation/import", json=payload)
    assert imported.status_code == 200
    result = imported.json()
    assert result["assignments_skipped"] == 0
    assert result["assignments_applied"] == 0  # already manual

    bad = await client.post(
        "/api/v1/curation/import", json={"version": 99}
    )
    assert bad.status_code == 400
