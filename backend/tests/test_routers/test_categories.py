from httpx import AsyncClient


async def test_list_categories_empty(client: AsyncClient):
    response = await client.get("/api/v1/categories")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_category(client: AsyncClient):
    response = await client.post("/api/v1/categories", json={
        "name": "Finance",
        "color": "#4CAF50",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Finance"
    assert data["slug"] == "finance"
    assert data["color"] == "#4CAF50"


async def test_create_child_category(client: AsyncClient):
    # Create parent
    parent = await client.post("/api/v1/categories", json={"name": "Work"})
    parent_id = parent.json()["id"]

    # Create child
    child = await client.post("/api/v1/categories", json={
        "name": "Reports",
        "parent_id": parent_id,
    })
    assert child.status_code == 201
    assert child.json()["parent_id"] == parent_id


async def test_update_category(client: AsyncClient):
    create_resp = await client.post("/api/v1/categories", json={"name": "Old Name"})
    cat_id = create_resp.json()["id"]

    update_resp = await client.patch(f"/api/v1/categories/{cat_id}", json={"name": "New Name"})
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "New Name"


async def test_delete_category(client: AsyncClient):
    create_resp = await client.post("/api/v1/categories", json={"name": "ToDelete"})
    cat_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/categories/{cat_id}")
    assert del_resp.status_code == 204


async def test_get_categories_tree_format(client: AsyncClient):
    await client.post("/api/v1/categories", json={"name": "TreeTest"})
    response = await client.get("/api/v1/categories", params={"format": "tree"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


async def test_get_categories_flat_format(client: AsyncClient):
    await client.post("/api/v1/categories", json={"name": "FlatTest"})
    response = await client.get("/api/v1/categories", params={"format": "flat"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
