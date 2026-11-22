"""Phase 4a saved view endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import ContentFact, Document, File, Source


async def _seed(session: AsyncSession) -> None:
    source = Source(path="/tmp/view-api", recursive=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:view", status="categorized")
    session.add(document)
    await session.flush()
    session.add_all(
        [
            ContentFact(
                content_hash="sha256:view",
                extractor_version="1",
                facts_version=2,
                doc_type="invoice",
                summary="Acme invoice",
                topics=["billing"],
            ),
            File(
                source_id=source.id,
                document_id=document.id,
                content_hash=document.content_hash,
                path="/tmp/view-api/invoice.pdf",
                filename="invoice.pdf",
                extension=".pdf",
                size_bytes=1,
                status="categorized",
            ),
        ]
    )
    await session.commit()


async def test_view_crud_and_evaluation(
    client: AsyncClient, session: AsyncSession
):
    await _seed(session)

    created = await client.post(
        "/api/v1/views",
        json={
            "name": "Invoices",
            "query": {"doc_types": ["invoice"]},
        },
    )
    assert created.status_code == 201
    view = created.json()
    assert view["name"] == "Invoices"
    assert view["query"]["doc_types"] == ["invoice"]
    assert view["query"]["clusters"] == "collapse"

    listed = await client.get("/api/v1/views")
    assert [item["id"] for item in listed.json()] == [view["id"]]

    files = await client.get(f"/api/v1/views/{view['id']}/files")
    assert files.status_code == 200
    body = files.json()
    assert body["total"] == 1
    assert body["files"][0]["filename"] == "invoice.pdf"

    preview = await client.post(
        "/api/v1/views/preview",
        json={"query": {"text": "acme"}},
    )
    assert preview.status_code == 200
    assert preview.json()["total"] == 1

    updated = await client.patch(
        f"/api/v1/views/{view['id']}", json={"name": "Bills", "pinned": True}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Bills"
    assert updated.json()["pinned"] is True

    deleted = await client.delete(f"/api/v1/views/{view['id']}")
    assert deleted.status_code == 204
    assert (await client.get("/api/v1/views")).json() == []


async def test_view_not_found(client: AsyncClient):
    assert (await client.get("/api/v1/views/9999")).status_code == 404
    assert (
        await client.get("/api/v1/views/9999/files")
    ).status_code == 404
    assert (
        await client.patch("/api/v1/views/9999", json={"name": "x"})
    ).status_code == 404
    assert (await client.delete("/api/v1/views/9999")).status_code == 404
