"""Phase 4a saved view query evaluation."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import (
    ContentFact,
    Document,
    DocumentCategory,
    File,
    Source,
)
from cabinet.db.repositories import CategoryRepository
from cabinet.schemas import ViewConceptFilter, ViewQuery
from cabinet.services.views import ViewQueryService


async def _document(
    session: AsyncSession,
    digest: str,
    *,
    doc_type: str | None = None,
    topics: list[str] | None = None,
    summary: str | None = None,
    cluster_id: int | None = None,
) -> Document:
    document = Document(
        content_hash=digest, status="categorized", cluster_id=cluster_id
    )
    session.add(document)
    await session.flush()
    if summary is not None or doc_type is not None or topics is not None:
        session.add(
            ContentFact(
                content_hash=digest,
                extractor_version="1",
                facts_version=2,
                doc_type=doc_type,
                topics=topics,
                summary=summary,
            )
        )
        await session.flush()
    return document


def _file(
    source_id: int,
    document: Document,
    name: str,
    *,
    modified: datetime | None = None,
) -> File:
    return File(
        source_id=source_id,
        document_id=document.id,
        content_hash=document.content_hash,
        path=f"/tmp/views/{name}",
        filename=name,
        extension=".pdf",
        size_bytes=1,
        status="categorized",
        file_modified_at=modified,
    )


async def test_doc_type_topic_and_text_filters(session: AsyncSession):
    source = Source(path="/tmp/views", recursive=True)
    session.add(source)
    await session.flush()

    invoice = await _document(
        session,
        "sha256:inv",
        doc_type="invoice",
        topics=["billing"],
        summary="Acme invoice for consulting",
    )
    note = await _document(
        session,
        "sha256:note",
        doc_type="note",
        topics=["travel"],
        summary="Trip notes",
    )
    session.add_all(
        [
            _file(source.id, invoice, "invoice.pdf"),
            _file(source.id, note, "notes.pdf"),
        ]
    )
    await session.flush()

    service = ViewQueryService(session)

    by_type = await service.page(ViewQuery(doc_types=["INVOICE"]))
    assert by_type.total == 1
    assert by_type.files[0].filename == "invoice.pdf"

    by_topic = await service.page(ViewQuery(topics=["travel"]))
    assert by_topic.total == 1
    assert by_topic.files[0].filename == "notes.pdf"

    by_text = await service.page(ViewQuery(text="acme"))
    assert by_text.total == 1
    assert by_text.files[0].filename == "invoice.pdf"


async def test_concept_filters_expand_descendants(session: AsyncSession):
    source = Source(path="/tmp/views", recursive=True)
    session.add(source)
    await session.flush()

    parent = await CategoryRepository.create(session, name="Finance")
    child = await CategoryRepository.create(
        session, name="Invoices", parent_id=parent.id
    )
    travel = await CategoryRepository.create(session, name="Travel")

    document = await _document(session, "sha256:inv", summary="invoice")
    other = await _document(session, "sha256:other", summary="other")
    session.add_all(
        [
            _file(source.id, document, "invoice.pdf"),
            _file(source.id, other, "other.pdf"),
        ]
    )
    session.add(
        DocumentCategory(document_id=document.id, category_id=child.id)
    )
    await session.flush()

    service = ViewQueryService(session)

    with_children = await service.page(
        ViewQuery(
            concepts=ViewConceptFilter(
                ids=[parent.id], include_descendants=True
            )
        )
    )
    assert with_children.total == 1
    assert with_children.files[0].filename == "invoice.pdf"

    exact = await service.page(
        ViewQuery(
            concepts=ViewConceptFilter(
                ids=[parent.id], include_descendants=False
            )
        )
    )
    assert exact.total == 0

    both = await service.page(
        ViewQuery(
            concepts=ViewConceptFilter(
                ids=[parent.id, child.id], match="all"
            )
        )
    )
    assert both.total == 1

    impossible = await service.page(
        ViewQuery(
            concepts=ViewConceptFilter(
                ids=[parent.id, travel.id], match="all"
            )
        )
    )
    assert impossible.total == 0


async def test_cluster_collapse_counts_once(session: AsyncSession):
    source = Source(path="/tmp/views", recursive=True)
    session.add(source)
    await session.flush()

    first = await _document(
        session, "sha256:v1", summary="report alpha", doc_type="report"
    )
    second = await _document(
        session, "sha256:v2", summary="report beta", doc_type="report"
    )
    first.cluster_id = first.id
    second.cluster_id = first.id
    session.add_all(
        [
            _file(
                source.id, first, "report.pdf", modified=datetime(2026, 1, 1)
            ),
            _file(
                source.id, second, "report_v2.pdf", modified=datetime(2026, 1, 2)
            ),
        ]
    )
    await session.flush()

    service = ViewQueryService(session)

    collapsed = await service.page(ViewQuery(text="report"))
    assert collapsed.total == 1
    assert collapsed.files[0].filename == "report_v2.pdf"  # newest
    assert collapsed.version_counts[collapsed.files[0].id] == 2

    separate = await service.page(
        ViewQuery(text="report", clusters="separate")
    )
    assert separate.total == 2


async def test_facets_and_groups(session: AsyncSession):
    source = Source(path="/tmp/views", label="Docs", recursive=True)
    session.add(source)
    await session.flush()

    invoice = await _document(
        session,
        "sha256:inv",
        doc_type="invoice",
        topics=["billing", "urgent"],
        summary="invoice",
    )
    note = await _document(
        session,
        "sha256:note",
        doc_type="note",
        topics=["travel"],
        summary="note",
    )
    invoice2 = await _document(
        session,
        "sha256:inv2",
        doc_type="invoice",
        topics=["billing"],
        summary="invoice two",
    )
    finance = await CategoryRepository.create(session, name="Finance")
    travel = await CategoryRepository.create(session, name="Travel")
    session.add_all(
        [
            _file(source.id, invoice, "invoice.pdf", modified=datetime(2026, 1, 15)),
            _file(source.id, note, "notes.pdf", modified=datetime(2026, 2, 10)),
            _file(source.id, invoice2, "invoice2.pdf", modified=datetime(2026, 1, 20)),
        ]
    )
    session.add_all(
        [
            DocumentCategory(document_id=invoice.id, category_id=finance.id),
            DocumentCategory(document_id=invoice2.id, category_id=finance.id),
            DocumentCategory(document_id=note.id, category_id=travel.id),
        ]
    )
    await session.flush()

    service = ViewQueryService(session)
    facets = await service.facets(ViewQuery())

    assert facets.total == 3
    assert {f.value: f.count for f in facets.doc_types} == {
        "invoice": 2,
        "note": 1,
    }
    assert {f.value: f.count for f in facets.topics} == {
        "billing": 2,
        "urgent": 1,
        "travel": 1,
    }
    assert facets.sources[0].label == "Docs"
    assert facets.sources[0].count == 3
    assert {f.value: f.count for f in facets.months} == {
        "2026-01": 2,
        "2026-02": 1,
    }

    by_type = await service.groups(ViewQuery(), group_by="doc_type")
    assert {g.label: g.count for g in by_type.groups} == {
        "invoice": 2,
        "note": 1,
    }
    assert by_type.total == 3

    by_concept = await service.groups(ViewQuery(), group_by="concept")
    assert {g.label: g.count for g in by_concept.groups} == {
        "Finance": 2,
        "Travel": 1,
    }
