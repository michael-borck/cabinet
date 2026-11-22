"""Server-side file search over filename + cached facts (design §5.3)."""

from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, File, Source
from cabinet.db.repositories import ContentFactRepository, FileRepository


async def _seed(session: AsyncSession) -> None:
    source = Source(path="/tmp/search-src", recursive=True, store_facts=True)
    session.add(source)
    await session.flush()

    docs = {
        "sha256:inv": Document(content_hash="sha256:inv", status="categorized"),
        "sha256:notes": Document(content_hash="sha256:notes", status="categorized"),
        "sha256:rep": Document(content_hash="sha256:rep", status="categorized"),
    }
    session.add_all(docs.values())
    await session.flush()

    session.add_all(
        [
            File(
                source_id=source.id,
                document_id=docs["sha256:inv"].id,
                content_hash="sha256:inv",
                path="/tmp/search-src/invoice-2026.pdf",
                filename="invoice-2026.pdf",
                extension=".pdf",
                size_bytes=1,
                status="categorized",
            ),
            File(
                source_id=source.id,
                document_id=docs["sha256:notes"].id,
                content_hash="sha256:notes",
                path="/tmp/search-src/notes.txt",
                filename="notes.txt",
                extension=".txt",
                size_bytes=1,
                status="categorized",
            ),
            File(
                source_id=source.id,
                document_id=docs["sha256:rep"].id,
                content_hash="sha256:rep",
                path="/tmp/search-src/report.pdf",
                filename="report.pdf",
                extension=".pdf",
                size_bytes=1,
                status="categorized",
            ),
        ]
    )
    await session.flush()

    await ContentFactRepository.upsert(
        session,
        content_hash="sha256:inv",
        extractor_version="1",
        facts_version=1,
        summary="Consulting invoice for the first quarter",
        doc_type="invoice",
        topics=["billing"],
    )
    await ContentFactRepository.upsert(
        session,
        content_hash="sha256:notes",
        extractor_version="1",
        facts_version=1,
        summary="Meeting notes about the roadmap",
        doc_type="note",
        topics=["roadmap"],
    )
    await ContentFactRepository.upsert(
        session,
        content_hash="sha256:rep",
        extractor_version="1",
        facts_version=1,
        summary="Quarterly financial report",
        doc_type="report",
        topics=["finance"],
    )
    await session.commit()


async def test_query_matches_filename(session: AsyncSession):
    await _seed(session)
    rows, total = await FileRepository.list_files(session, query="invoice-2026")
    assert total == 1
    assert [f.filename for f in rows] == ["invoice-2026.pdf"]


async def test_query_matches_summary_and_topics(session: AsyncSession):
    await _seed(session)

    rows, total = await FileRepository.list_files(session, query="financial")
    assert total == 1
    assert [f.filename for f in rows] == ["report.pdf"]

    # JSON topics column is searched too.
    rows, total = await FileRepository.list_files(session, query="roadmap")
    assert total == 1
    assert [f.filename for f in rows] == ["notes.txt"]


async def test_query_matches_doc_type(session: AsyncSession):
    await _seed(session)
    rows, total = await FileRepository.list_files(session, query="invoice")
    assert total == 1  # one row even though filename and summary both match
    assert [f.filename for f in rows] == ["invoice-2026.pdf"]


async def test_query_no_match(session: AsyncSession):
    await _seed(session)
    rows, total = await FileRepository.list_files(session, query="zzz-not-there")
    assert total == 0
    assert list(rows) == []


async def test_query_combines_with_filters(session: AsyncSession):
    await _seed(session)
    rows, total = await FileRepository.list_files(
        session, query="invoice", extension=".txt"
    )
    assert total == 0
    assert list(rows) == []
