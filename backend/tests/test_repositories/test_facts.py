"""Tests for ContentFactRepository (Phase 2a, design §5)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import ContentFact, Document, File, Source
from cabinet.db.repositories import ContentFactRepository


async def test_upsert_get_and_freshness(session: AsyncSession):
    fact = await ContentFactRepository.upsert(
        session,
        content_hash="abc",  # legacy bare hex normalises to sha256:
        extractor_version="1",
        facts_version=1,
        summary="first",
        topics=["a"],
    )
    await session.commit()

    assert fact.content_hash == "sha256:abc"
    fetched = await ContentFactRepository.get(session, "sha256:abc")
    assert fetched is not None and fetched.summary == "first"
    assert ContentFactRepository.is_fresh(
        fetched, extractor_version="1", facts_version=1
    )
    assert not ContentFactRepository.is_fresh(
        fetched, extractor_version="2", facts_version=1
    )
    assert not ContentFactRepository.is_fresh(
        fetched, extractor_version="1", facts_version=2
    )

    # Upsert replaces in place rather than creating a second row.
    await ContentFactRepository.upsert(
        session,
        content_hash="sha256:abc",
        extractor_version="1",
        facts_version=1,
        summary="second",
    )
    await session.commit()

    rows = (await session.execute(select(ContentFact))).scalars().all()
    assert len(rows) == 1
    assert (await ContentFactRepository.get(session, "sha256:abc")).summary == "second"


async def test_purge_unreferenced_keeps_referenced(
    session: AsyncSession, tmp_path
):
    source = Source(path=str(tmp_path), recursive=True, store_facts=True)
    session.add(source)
    await session.flush()

    kept_doc = Document(content_hash="sha256:keep", status="categorized")
    drop_doc = Document(content_hash="sha256:drop", status="categorized")
    session.add_all([kept_doc, drop_doc])
    await session.flush()

    session.add(
        File(
            source_id=source.id,
            document_id=kept_doc.id,
            content_hash="sha256:keep",
            path=str(tmp_path / "keep.txt"),
            filename="keep.txt",
            size_bytes=1,
            status="categorized",
        )
    )
    for digest in ("sha256:keep", "sha256:drop"):
        await ContentFactRepository.upsert(
            session,
            content_hash=digest,
            extractor_version="1",
            facts_version=1,
            summary=digest,
        )
    await session.commit()

    removed = await ContentFactRepository.purge_unreferenced(session)
    await session.commit()

    assert removed == 1
    assert await ContentFactRepository.get(session, "sha256:keep") is not None
    assert await ContentFactRepository.get(session, "sha256:drop") is None
