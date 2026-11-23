"""Phase 5a portable curation sidecar."""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cabinet.db.models import Base, Document, DocumentCategory
from cabinet.db.repositories import CategoryRepository, ViewRepository
from cabinet.services.curation import export_data, import_data, write_sidecar
from cabinet.utils.concepts import normalize_concept


async def _seed(session: AsyncSession) -> None:
    finance = await CategoryRepository.create(
        session, name="Finance", definition="Money"
    )
    invoices = await CategoryRepository.create(
        session, name="Invoices", parent_id=finance.id
    )
    await CategoryRepository.add_alias(session, invoices.id, "Billing")

    bills = await CategoryRepository.create(session, name="Bills")
    await CategoryRepository.merge(session, bills.id, invoices.id)

    document = Document(content_hash="sha256:doc1", status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        DocumentCategory(
            document_id=document.id, category_id=invoices.id, is_manual=True
        )
    )
    await ViewRepository.create(
        session, name="Q1", query={"doc_types": ["invoice"]}, pinned=True
    )
    await session.commit()


async def test_export_contains_curation(session: AsyncSession):
    await _seed(session)

    data = await export_data(session)

    paths = {concept["path"] for concept in data["concepts"]}
    assert {"Finance", "Finance > Invoices"} <= paths
    invoices = next(
        concept
        for concept in data["concepts"]
        if concept["path"] == "Finance > Invoices"
    )
    assert set(invoices["aliases"]) == {"Billing", "Bills"}
    assert data["merges"] == [
        {"source_path": "Bills", "target_path": "Finance > Invoices"}
    ]
    assert data["assignments"] == [
        {
            "content_hash": "sha256:doc1",
            "concept_path": "Finance > Invoices",
        }
    ]
    assert data["views"][0]["name"] == "Q1"


async def test_import_round_trip_and_idempotent(session: AsyncSession):
    await _seed(session)
    payload = await export_data(session)

    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as other:
            other.add(
                Document(content_hash="sha256:doc1", status="categorized")
            )
            await other.flush()

            result = await import_data(other, payload)
            await other.commit()

            assert result["concepts_created"] == 2
            assert result["assignments_applied"] == 1
            assert result["merges_applied"] == 0  # restored as an alias
            assert result["views_created"] == 1
            assert result["aliases_added"] == 2

            billing = await CategoryRepository.get_by_normalized_alias(
                other, normalize_concept("Billing")
            )
            assert billing is not None
            assert billing.name == "Invoices"
            # The merged source name is restored as an alias of the winner.
            bills = await CategoryRepository.get_by_normalized_alias(
                other, normalize_concept("Bills")
            )
            assert bills is not None and bills.id == billing.id

            assignments = (
                await other.execute(select(DocumentCategory))
            ).scalars().all()
            assert len(assignments) == 1
            assert assignments[0].is_manual is True

            views = await ViewRepository.list_all(other)
            assert [view.name for view in views] == ["Q1"]

            # A second import is a no-op.
            second = await import_data(other, payload)
            await other.commit()
            assert second["concepts_created"] == 0
            assert second["aliases_added"] == 0
            assert second["assignments_applied"] == 0
            assert second["merges_applied"] == 0
            assert second["views_created"] == 0
    finally:
        await engine.dispose()


async def test_unknown_hash_is_reported(session: AsyncSession):
    result = await import_data(
        session,
        {
            "version": 1,
            "assignments": [
                {
                    "content_hash": "sha256:missing",
                    "concept_path": "Finance",
                }
            ],
        },
    )
    await session.commit()

    assert result["assignments_skipped"] == 1
    assert result["assignments_applied"] == 0
    assert result["warnings"]


async def test_write_sidecar_is_atomic_json(session: AsyncSession, tmp_path):
    await _seed(session)
    path = tmp_path / "curation.json"

    written = await write_sidecar(session, str(path))

    assert written == str(path)
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert data["assignments"]
    assert not (tmp_path / "curation.json.tmp").exists()


async def test_import_merge_without_existing_alias(session: AsyncSession):
    payload = {
        "version": 1,
        "concepts": [
            {"path": "Finance", "name": "Finance", "status": "approved"}
        ],
        "merges": [{"source_path": "Legacy", "target_path": "Finance"}],
    }

    result = await import_data(session, payload)
    await session.commit()

    assert result["merges_applied"] == 1
    legacy = await CategoryRepository.get_by_normalized_alias(
        session, normalize_concept("Legacy")
    )
    assert legacy is not None
    assert legacy.name == "Finance"
