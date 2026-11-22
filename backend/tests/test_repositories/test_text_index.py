"""Tier-2 full-text index tests (Phase 2b, design §5.3/§5.4)."""

from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, File, Source
from cabinet.db.repositories import TextIndexRepository


async def test_upsert_search_delete(session: AsyncSession):
    await TextIndexRepository.upsert(session, "sha256:abc", "the quick brown fox")
    await session.commit()

    assert await TextIndexRepository.has(session, "sha256:abc")
    assert await TextIndexRepository.search_hashes(session, "brown") == ["sha256:abc"]
    assert await TextIndexRepository.search_hashes(session, "zebra") == []

    # Upsert replaces rather than duplicating.
    await TextIndexRepository.upsert(session, "sha256:abc", "a different document")
    await session.commit()
    assert await TextIndexRepository.search_hashes(session, "brown") == []
    assert await TextIndexRepository.search_hashes(session, "different") == [
        "sha256:abc"
    ]

    await TextIndexRepository.delete(session, "sha256:abc")
    await session.commit()
    assert not await TextIndexRepository.has(session, "sha256:abc")


async def test_search_terms_are_anded(session: AsyncSession):
    await TextIndexRepository.upsert(session, "sha256:a", "invoice one")
    await TextIndexRepository.upsert(session, "sha256:b", "meeting notes")
    await session.commit()

    assert await TextIndexRepository.search_hashes(session, "invoice") == ["sha256:a"]
    assert await TextIndexRepository.search_hashes(session, "invoice notes") == []


async def test_purge_unreferenced_keeps_indexed_sources(
    session: AsyncSession, tmp_path
):
    source = Source(path=str(tmp_path), recursive=True, store_text_index=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash="sha256:keep", status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        File(
            source_id=source.id,
            document_id=document.id,
            content_hash=document.content_hash,
            path=str(tmp_path / "keep.txt"),
            filename="keep.txt",
            size_bytes=1,
            status="categorized",
        )
    )
    await TextIndexRepository.upsert(session, "sha256:keep", "keep me")
    await TextIndexRepository.upsert(session, "sha256:drop", "drop me")
    await session.commit()

    removed = await TextIndexRepository.purge_unreferenced(session)
    await session.commit()

    assert removed == 1
    assert await TextIndexRepository.has(session, "sha256:keep")
    assert not await TextIndexRepository.has(session, "sha256:drop")
