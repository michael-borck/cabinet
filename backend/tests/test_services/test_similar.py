"""Phase 4c similar-document ranking."""

from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import ContentFact, Document, File, Source
from cabinet.services.similar import SimilarDocumentsService
from cabinet.utils.embeddings import pack_embedding


async def _add(
    session: AsyncSession,
    source_id: int,
    digest: str,
    embedding: list[float] | None,
    summary: str,
) -> File:
    document = Document(content_hash=digest, status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        ContentFact(
            content_hash=digest,
            extractor_version="1",
            facts_version=2,
            summary=summary,
            doc_type="note",
            topics=["topic"],
            embedding=pack_embedding(embedding) if embedding else None,
        )
    )
    file = File(
        source_id=source_id,
        document_id=document.id,
        content_hash=digest,
        path=f"/tmp/similar/{digest}.txt",
        filename=f"{digest}.txt",
        extension=".txt",
        size_bytes=1,
        status="categorized",
    )
    session.add(file)
    await session.flush()
    return file


async def test_similar_orders_by_cosine_and_excludes_source(
    session: AsyncSession,
):
    source = Source(path="/tmp/similar", recursive=True)
    session.add(source)
    await session.flush()

    origin = await _add(session, source.id, "sha256:a", [1.0, 0.0], "origin")
    close = await _add(session, source.id, "sha256:b", [0.9, 0.1], "close")
    far = await _add(session, source.id, "sha256:c", [0.0, 1.0], "far")

    result = await SimilarDocumentsService(session).for_file(origin.id)

    assert result is not None
    ids = [item.document_id for item in result.items]
    assert result.source_document_id == origin.document_id
    assert origin.document_id not in ids
    assert len(result.items) == 2
    assert result.items[0].document_id == close.document_id
    assert result.items[1].document_id == far.document_id
    assert result.items[0].similarity > result.items[1].similarity
    assert result.items[0].filename == "sha256:b.txt"


async def test_missing_embedding_returns_empty(session: AsyncSession):
    source = Source(path="/tmp/similar", recursive=True)
    session.add(source)
    await session.flush()

    file = await _add(session, source.id, "sha256:x", None, "no vector")

    result = await SimilarDocumentsService(session).for_file(file.id)

    assert result is not None
    assert result.items == []
