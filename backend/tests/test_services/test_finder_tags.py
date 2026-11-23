"""Phase 5c macOS Finder tag export."""

import sys

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, DocumentCategory, File, Source
from cabinet.db.repositories import CategoryRepository
from cabinet.services.finder_tags import FinderTagsService, read_raw_tags, write_raw_tags

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="Finder tags are macOS-only"
)


async def _seed(session: AsyncSession, tmp_path, *, digest: str, name: str):
    source_dir = tmp_path / "src"
    source_dir.mkdir(exist_ok=True)
    target = source_dir / name
    target.write_text("content")
    source = Source(path=str(source_dir), recursive=True)
    session.add(source)
    await session.flush()
    document = Document(content_hash=digest, status="categorized")
    session.add(document)
    await session.flush()
    session.add(
        File(
            source_id=source.id,
            document_id=document.id,
            content_hash=digest,
            path=str(target),
            filename=name,
            extension=".txt",
            size_bytes=7,
            status="categorized",
        )
    )
    category = await CategoryRepository.create(session, name="Finance")
    session.add(
        DocumentCategory(document_id=document.id, category_id=category.id)
    )
    await session.flush()
    return target


def _read(path) -> set[str]:
    return {value.split("\n", 1)[0] for value in read_raw_tags(str(path))}


async def test_plan_and_apply_adds_concept_tag(session: AsyncSession, tmp_path):
    target = await _seed(session, tmp_path, digest="sha256:a", name="a.txt")
    service = FinderTagsService(session)

    plan = await service.plan()
    assert plan.supported is True
    assert plan.files == 1
    assert plan.tagged == 1

    result = await service.apply()
    assert result.tagged == 1
    assert result.errors == []
    assert _read(target) == {"Finance"}

    again = await service.plan()
    assert again.tagged == 0
    assert again.skipped == 1


async def test_existing_tags_are_preserved(session: AsyncSession, tmp_path):
    target = await _seed(session, tmp_path, digest="sha256:b", name="b.txt")
    write_raw_tags(str(target), ["Keep\n4"])

    await FinderTagsService(session).apply()

    values = read_raw_tags(str(target))
    assert "Keep\n4" in values
    assert "Finance" in [value.split("\n", 1)[0] for value in values]


async def test_unsupported_platform_reports_cleanly(
    session: AsyncSession, tmp_path, monkeypatch
):
    await _seed(session, tmp_path, digest="sha256:c", name="c.txt")
    monkeypatch.setattr("cabinet.services.finder_tags.sys.platform", "linux")

    plan = await FinderTagsService(session).plan()
    result = await FinderTagsService(session).apply()

    assert plan.supported is False
    assert "macOS" in (plan.message or "")
    assert result.supported is False
