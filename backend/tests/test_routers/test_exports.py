"""Phase 5b symlink export endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, DocumentCategory, File, Source
from cabinet.db.repositories import CategoryRepository


async def test_symlink_export_endpoints(
    client: AsyncClient, session: AsyncSession, tmp_path
):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    original = source_dir / "a.txt"
    original.write_text("content")

    source = Source(path=str(source_dir), recursive=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:a", status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        File(
            source_id=source.id,
            document_id=document.id,
            content_hash=document.content_hash,
            path=str(original),
            filename="a.txt",
            extension=".txt",
            size_bytes=7,
            status="categorized",
        )
    )
    category = await CategoryRepository.create(session, name="Work")
    session.add(
        DocumentCategory(document_id=document.id, category_id=category.id)
    )
    await session.commit()

    target = tmp_path / "out"
    body = {"target_dir": str(target), "scope": "library"}

    plan = await client.post("/api/v1/exports/symlink-tree/plan", json=body)
    assert plan.status_code == 200
    assert plan.json()["supported"] is True
    assert plan.json()["creates"] == 1

    applied = await client.post("/api/v1/exports/symlink-tree/apply", json=body)
    assert applied.status_code == 200
    assert applied.json()["created"] == 1
    assert (target / "work" / "a.txt").is_symlink()

    not_a_dir = tmp_path / "afile"
    not_a_dir.write_text("x")
    bad = await client.post(
        "/api/v1/exports/symlink-tree/plan",
        json={"target_dir": str(not_a_dir), "scope": "library"},
    )
    assert bad.status_code == 400
