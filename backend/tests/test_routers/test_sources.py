import os
import tempfile
from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient


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
