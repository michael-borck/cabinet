"""Tests for per-parent category uniqueness and cycle prevention."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.repositories import CategoryRepository


async def test_same_name_under_two_parents(session: AsyncSession):
    """'Reports' may exist under both 'Finance' and 'Projects'."""
    finance_reports = await CategoryRepository.get_or_create_from_path(
        session, "Finance > Reports"
    )
    projects_reports = await CategoryRepository.get_or_create_from_path(
        session, "Projects > Reports"
    )
    assert finance_reports.id != projects_reports.id
    assert finance_reports.slug == projects_reports.slug

    # Resolving the same path again returns the existing category.
    again = await CategoryRepository.get_or_create_from_path(
        session, "Finance > Reports"
    )
    assert again.id == finance_reports.id


async def test_self_parenting_rejected(session: AsyncSession):
    cat = await CategoryRepository.create(session, name="Alpha")
    with pytest.raises(ValueError):
        await CategoryRepository.update(session, cat.id, parent_id=cat.id)


async def test_cycle_rejected_via_api(client: AsyncClient):
    parent = (await client.post("/api/v1/categories", json={"name": "Parent"})).json()
    child = (
        await client.post(
            "/api/v1/categories", json={"name": "Child", "parent_id": parent["id"]}
        )
    ).json()

    response = await client.patch(
        f"/api/v1/categories/{parent['id']}", json={"parent_id": child["id"]}
    )
    assert response.status_code == 400
