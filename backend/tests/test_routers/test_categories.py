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


async def test_create_category_with_definition_and_governance_fields(
    client: AsyncClient,
):
    response = await client.post(
        "/api/v1/categories",
        json={"name": "Billing", "definition": "Invoices and receipts"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["definition"] == "Invoices and receipts"
    assert data["status"] == "approved"
    assert data["origin"] == "manual"
    assert data["aliases"] == []
    assert data["example_count"] == 0


async def test_category_alias_crud_and_conflict(client: AsyncClient):
    billing = (
        await client.post("/api/v1/categories", json={"name": "Billing"})
    ).json()
    finance = (
        await client.post("/api/v1/categories", json={"name": "Finance"})
    ).json()

    created = await client.post(
        f"/api/v1/categories/{billing['id']}/aliases",
        json={"alias": "Invoices"},
    )
    assert created.status_code == 201
    alias = created.json()
    assert alias["alias"] == "Invoices"
    assert alias["normalized"] == "invoice"

    listed = await client.get("/api/v1/categories")
    row = next(c for c in listed.json() if c["id"] == billing["id"])
    assert row["aliases"] == ["Invoices"]

    conflict = await client.post(
        f"/api/v1/categories/{finance['id']}/aliases",
        json={"alias": "Invoices"},
    )
    assert conflict.status_code == 409

    deleted = await client.delete(
        f"/api/v1/categories/{billing['id']}/aliases/{alias['id']}"
    )
    assert deleted.status_code == 204
    assert (
        await client.delete(
            f"/api/v1/categories/{billing['id']}/aliases/{alias['id']}"
        )
    ).status_code == 404


async def test_category_review_approve_and_reject(client: AsyncClient, session, tmp_path):
    from cabinet.db.models import (
        Document,
        DocumentCategory,
        File,
        Source,
    )
    from cabinet.db.repositories import CategoryRepository
    from cabinet.utils.embeddings import pack_embedding

    approved = await CategoryRepository.create(session, name="Invoice")
    await CategoryRepository.set_embedding(
        session,
        approved.id,
        embedding=pack_embedding([1.0, 0.0]),
        model="m",
        dim=2,
    )
    provisional = await CategoryRepository.create(
        session, name="Bills", status="provisional", origin="ai"
    )
    await CategoryRepository.set_embedding(
        session,
        provisional.id,
        embedding=pack_embedding([0.99, 0.05]),
        model="m",
        dim=2,
    )

    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:rev", status="pending")
    session.add(document)
    await session.flush()
    session.add(
        File(
            source_id=source.id,
            document_id=document.id,
            content_hash=document.content_hash,
            path=str(tmp_path / "bill.pdf"),
            filename="bill.pdf",
            size_bytes=1,
            status="pending",
        )
    )
    session.add(
        DocumentCategory(document_id=document.id, category_id=provisional.id)
    )
    await session.commit()

    review = await client.get("/api/v1/categories/review")
    assert review.status_code == 200
    item = next(i for i in review.json() if i["id"] == provisional.id)
    assert item["document_count"] == 1
    assert item["samples"][0]["filename"] == "bill.pdf"
    assert any(
        duplicate["category_id"] == approved.id
        for duplicate in item["possible_duplicates"]
    )

    approve = await client.post(
        f"/api/v1/categories/{provisional.id}/approve", json={}
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    temporary = await CategoryRepository.create(
        session, name="Temporary", status="provisional", origin="ai"
    )
    await session.commit()
    reject = await client.post(f"/api/v1/categories/{temporary.id}/reject")
    assert reject.status_code == 204
    after = await client.get("/api/v1/categories/review")
    assert all(entry["id"] != temporary.id for entry in after.json())


async def test_merge_endpoint(client: AsyncClient, session):
    from cabinet.db.repositories import CategoryRepository

    source = await CategoryRepository.create(session, name="Billing")
    target = await CategoryRepository.create(session, name="Invoices")
    await session.commit()

    response = await client.post(
        f"/api/v1/categories/{source.id}/merge",
        json={"target_id": target.id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == source.id
    assert body["target_id"] == target.id

    await session.refresh(source)
    assert source.status == "merged"
    assert source.merged_into_id == target.id


async def test_consolidate_endpoint_deterministic(client: AsyncClient, session):
    from cabinet.db.repositories import CategoryRepository
    from cabinet.utils.embeddings import pack_embedding

    invoice = await CategoryRepository.create(session, name="Invoice")
    bills = await CategoryRepository.create(session, name="Bills")
    await CategoryRepository.set_embedding(
        session,
        invoice.id,
        embedding=pack_embedding([1.0, 0.0]),
        model="m",
        dim=2,
    )
    await CategoryRepository.set_embedding(
        session,
        bills.id,
        embedding=pack_embedding([0.99, 0.05]),
        model="m",
        dim=2,
    )
    await session.commit()

    response = await client.post("/api/v1/categories/consolidate")
    assert response.status_code == 200
    merges = response.json()["merges"]
    assert any(
        {merge["source_id"], merge["target_id"]} == {invoice.id, bills.id}
        for merge in merges
    )
