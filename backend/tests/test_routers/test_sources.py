import os
import tempfile
from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Job, Source


@pytest_asyncio.fixture
async def test_dir():
    d = tempfile.mkdtemp()
    # Resolve symlinks so path matches backend (e.g. /var -> /private/var on macOS)
    resolved = str(Path(d).resolve())
    yield resolved
    os.rmdir(resolved)


async def test_list_sources_empty(client: AsyncClient):
    response = await client.get("/api/v1/sources")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_source(client: AsyncClient, test_dir: str):
    response = await client.post("/api/v1/sources", json={
        "path": test_dir,
        "label": "Test Folder",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["path"] == test_dir
    assert data["label"] == "Test Folder"
    assert data["recursive"] is True


async def test_create_source_invalid_path(client: AsyncClient):
    response = await client.post("/api/v1/sources", json={
        "path": "/nonexistent/path/that/does/not/exist",
    })
    assert response.status_code == 400


async def test_create_source_with_exclude_patterns(client: AsyncClient, test_dir: str):
    response = await client.post("/api/v1/sources", json={
        "path": test_dir,
        "exclude_patterns": ["*.log", "  drafts  ", ""],
    })
    assert response.status_code == 201
    # Whitespace is stripped and empty entries dropped.
    assert response.json()["exclude_patterns"] == ["*.log", "drafts"]


async def test_update_source_exclude_patterns(client: AsyncClient, test_dir: str):
    create_resp = await client.post("/api/v1/sources", json={"path": test_dir})
    source_id = create_resp.json()["id"]
    assert create_resp.json()["exclude_patterns"] == []

    patch_resp = await client.patch(f"/api/v1/sources/{source_id}", json={
        "exclude_patterns": ["*.tmp", "old"],
    })
    assert patch_resp.status_code == 200
    assert patch_resp.json()["exclude_patterns"] == ["*.tmp", "old"]

    # Partial update: omitting the field leaves patterns untouched.
    label_resp = await client.patch(f"/api/v1/sources/{source_id}", json={
        "label": "Renamed",
    })
    assert label_resp.json()["label"] == "Renamed"
    assert label_resp.json()["exclude_patterns"] == ["*.tmp", "old"]

    # Clearing with an empty list works.
    clear_resp = await client.patch(f"/api/v1/sources/{source_id}", json={
        "exclude_patterns": [],
    })
    assert clear_resp.json()["exclude_patterns"] == []


async def test_update_source_not_found(client: AsyncClient):
    response = await client.patch("/api/v1/sources/9999", json={"label": "x"})
    assert response.status_code == 404


async def test_delete_source(client: AsyncClient, test_dir: str):
    # Create first
    create_resp = await client.post("/api/v1/sources", json={"path": test_dir})
    source_id = create_resp.json()["id"]

    # Delete
    del_resp = await client.delete(f"/api/v1/sources/{source_id}")
    assert del_resp.status_code == 204

    # Verify gone
    list_resp = await client.get("/api/v1/sources")
    assert len(list_resp.json()) == 0


async def test_delete_source_not_found(client: AsyncClient):
    response = await client.delete("/api/v1/sources/9999")
    assert response.status_code == 404


async def test_scan_rejected_while_job_active(
    client: AsyncClient, session: AsyncSession, test_dir: str
):
    """Only one scan may run per source: a second request must 409."""
    create_resp = await client.post("/api/v1/sources", json={"path": test_dir})
    source_id = create_resp.json()["id"]

    session.add(Job(job_type="quick_sync", source_id=source_id, status="running"))
    await session.commit()

    response = await client.post(
        f"/api/v1/sources/{source_id}/scan", json={"mode": "quick_sync"}
    )
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


async def test_scan_allowed_after_job_finished(
    client: AsyncClient, session: AsyncSession, test_dir: str, monkeypatch
):
    """Terminal jobs don't block a new scan."""
    # Don't actually run the background scan: it opens its own session on the
    # global engine, not the test engine.
    from cabinet.routers import sources as sources_router

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(sources_router, "_run_scan_and_pipeline", noop)

    create_resp = await client.post("/api/v1/sources", json={"path": test_dir})
    source_id = create_resp.json()["id"]

    session.add(Job(job_type="quick_sync", source_id=source_id, status="completed"))
    await session.commit()

    response = await client.post(
        f"/api/v1/sources/{source_id}/scan", json={"mode": "quick_sync"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "pending"


async def test_create_source_defaults_store_facts_on(client: AsyncClient, test_dir: str):
    response = await client.post("/api/v1/sources", json={"path": test_dir})
    assert response.status_code == 201
    assert response.json()["store_facts"] is True


async def test_disable_store_facts_purges_unreferenced_facts(
    client: AsyncClient, session: AsyncSession, test_dir: str
):
    from sqlalchemy import select

    from cabinet.db.models import Document, File
    from cabinet.db.repositories import ContentFactRepository

    response = await client.post("/api/v1/sources", json={"path": test_dir})
    source_id = response.json()["id"]

    doc = Document(content_hash="sha256:facts1", status="categorized")
    session.add(doc)
    await session.flush()
    session.add(
        File(
            source_id=source_id,
            document_id=doc.id,
            content_hash=doc.content_hash,
            path=f"{test_dir}/a.txt",
            filename="a.txt",
            size_bytes=1,
            status="categorized",
        )
    )
    await ContentFactRepository.upsert(
        session,
        content_hash="sha256:facts1",
        extractor_version="1",
        facts_version=1,
        summary="s",
    )
    await session.commit()

    patch = await client.patch(
        f"/api/v1/sources/{source_id}", json={"store_facts": False}
    )
    assert patch.status_code == 200
    assert patch.json()["store_facts"] is False

    session.expire_all()
    assert await ContentFactRepository.get(session, "sha256:facts1") is None
    remaining = (await session.execute(select(Document))).scalars().all()
    assert len(remaining) == 1  # identity is untouched; only facts are purged


async def test_create_source_defaults_store_text_index_off(
    client: AsyncClient, test_dir: str
):
    response = await client.post("/api/v1/sources", json={"path": test_dir})
    assert response.status_code == 201
    assert response.json()["store_text_index"] is False


async def test_disable_store_text_index_purges_unreferenced_text(
    client: AsyncClient, session: AsyncSession, test_dir: str
):
    from cabinet.db.models import Document, File
    from cabinet.db.repositories import TextIndexRepository

    response = await client.post("/api/v1/sources", json={"path": test_dir})
    source_id = response.json()["id"]
    enable = await client.patch(
        f"/api/v1/sources/{source_id}", json={"store_text_index": True}
    )
    assert enable.json()["store_text_index"] is True

    doc = Document(content_hash="sha256:text1", status="categorized")
    session.add(doc)
    await session.flush()
    session.add(
        File(
            source_id=source_id,
            document_id=doc.id,
            content_hash=doc.content_hash,
            path=f"{test_dir}/a.txt",
            filename="a.txt",
            size_bytes=1,
            status="categorized",
        )
    )
    await TextIndexRepository.upsert(session, "sha256:text1", "searchable body")
    await session.commit()
    assert await TextIndexRepository.has(session, "sha256:text1")

    patch = await client.patch(
        f"/api/v1/sources/{source_id}", json={"store_text_index": False}
    )
    assert patch.status_code == 200
    assert patch.json()["store_text_index"] is False

    session.expire_all()
    assert not await TextIndexRepository.has(session, "sha256:text1")


async def test_scan_commits_job_before_background_task(
    session: AsyncSession, engine, test_dir: str
):
    """Regression: the scan task opens a new session; the job must be visible.

    Dependency teardown runs after the background task on a real server, so
    the endpoint has to commit the job row itself before scheduling.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from starlette.background import BackgroundTasks

    from cabinet.routers.sources import scan_source
    from cabinet.schemas import ScanRequest

    source = Source(path=test_dir, recursive=True)
    session.add(source)
    await session.commit()

    job = await scan_source(
        source.id,
        ScanRequest(mode="quick_sync"),
        BackgroundTasks(),
        session,
    )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as other:
        seen = await other.get(Job, job.id)
        assert seen is not None
