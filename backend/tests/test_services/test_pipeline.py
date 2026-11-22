"""Tests for pipeline status handling."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import ContentFact, Document, File, Job, Source
from cabinet.db.repositories import ContentFactRepository
from cabinet.extractors.base import ExtractionResult
from cabinet.services.pipeline import PipelineService


async def _setup(session: AsyncSession, tmp_path) -> tuple[Source, Job, File]:
    target = tmp_path / "doc.txt"
    target.write_text("some content")

    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()

    file = File(
        source_id=source.id,
        path=str(target),
        filename="doc.txt",
        extension=".txt",
        size_bytes=12,
        status="pending",
    )
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add_all([file, job])
    await session.commit()
    return source, job, file


async def test_no_llm_config_leaves_files_extracted(session: AsyncSession, tmp_path):
    """Without an LLM config, files must NOT be marked categorized."""
    _, job, file = await _setup(session, tmp_path)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(file)
    await session.refresh(job)
    assert file.status == "extracted"
    assert job.status == "completed"


async def test_duplicate_instances_processed_once_per_document(
    session: AsyncSession, tmp_path
):
    """Two instances of one document = one pipeline unit (Phase 1a)."""
    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()
    doc = Document(content_hash="sha256:dup", status="pending")
    session.add(doc)
    await session.flush()

    files = []
    for name in ("a.txt", "copy.txt"):
        target = tmp_path / name
        target.write_text("same content")
        files.append(
            File(
                source_id=source.id,
                document_id=doc.id,
                path=str(target),
                filename=name,
                extension=".txt",
                size_bytes=12,
                status="pending",
            )
        )
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add_all([*files, job])
    await session.commit()

    await PipelineService(session).process_pending_files(job.id)

    await session.refresh(job)
    assert job.total_files == 1  # one document, not two instances
    statuses = (
        await session.execute(select(File.status).order_by(File.id))
    ).scalars().all()
    assert statuses == ["extracted", "extracted"]  # mirrored to both
    await session.refresh(doc)
    assert doc.status == "extracted"


async def test_extracted_files_are_retried(session: AsyncSession, tmp_path):
    """Files stranded in 'extracted' are picked up by the next run."""
    _, job, file = await _setup(session, tmp_path)
    file.status = "extracted"
    await session.commit()

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(job)
    assert job.total_files == 1  # the extracted file was selected


async def test_cancelled_job_is_not_resurrected(session: AsyncSession, tmp_path):
    """A cancelled job must stay cancelled, not flip to 'completed'."""
    _, job, _ = await _setup(session, tmp_path)
    job.status = "cancelled"
    await session.commit()

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(job)
    assert job.status == "cancelled"


async def test_extraction_error_recorded(session: AsyncSession, tmp_path):
    """An unreadable file is marked 'error' and counted on the job."""
    _, job, file = await _setup(session, tmp_path)
    file.path = str(tmp_path / "does-not-exist.txt")
    await session.commit()

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    await session.refresh(file)
    await session.refresh(job)
    assert file.status == "error"
    assert job.error_count == 1
    assert job.status == "completed_with_errors"
    assert "extract" in (job.error_log or "")


async def test_progress_is_committed(session: AsyncSession, tmp_path, engine):
    """Job progress must be visible to other sessions during/after the run."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _, job, _ = await _setup(session, tmp_path)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    other_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with other_factory() as other:
        seen = (
            await other.execute(select(Job).where(Job.id == job.id))
        ).scalar_one()
        assert seen.status == "completed"
        assert seen.processed_files == 1


async def test_failed_batch_does_not_abort_run(
    session: AsyncSession, tmp_path, monkeypatch
):
    """A categorization failure in one batch must not kill later batches.

    Regression test: the rollback in the failure handler expires every File
    instance in the session, which used to crash the next batch's extraction.
    """
    source = Source(path=str(tmp_path), recursive=True)
    session.add(source)
    await session.flush()

    files = []
    for name in ("a.txt", "b.txt"):
        target = tmp_path / name
        target.write_text("content of " + name)
        files.append(
            File(
                source_id=source.id,
                path=str(target),
                filename=name,
                extension=".txt",
                size_bytes=12,
                status="pending",
            )
        )
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add_all([*files, job])
    await session.commit()

    calls: list[list[int]] = []

    async def flaky_categorize(self, batch, extracted_texts, **kwargs):
        calls.append([f.id for f in batch])
        if len(calls) == 1:
            raise RuntimeError("LLM exploded")
        return {f.id for f in batch}

    monkeypatch.setattr(PipelineService, "_categorize_batch", flaky_categorize)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id, batch_size=1)

    await session.refresh(job)
    assert len(calls) == 2  # the second batch still ran
    assert job.status == "completed_with_errors"
    assert job.error_count == 1
    assert "[categorize]" in (job.error_log or "")

    statuses = (
        await session.execute(select(File.status).order_by(File.id))
    ).scalars().all()
    # Failed batch keeps its extraction result and will retry next run;
    # the following batch categorized normally.
    assert statuses == ["extracted", "categorized"]


async def test_placeholder_is_parked_not_extracted(
    session: AsyncSession, tmp_path, monkeypatch
):
    """A file that dehydrates before processing is deferred, never read."""
    import cabinet.services.pipeline as pipeline_module

    _, job, file = await _setup(session, tmp_path)

    monkeypatch.setattr(
        pipeline_module, "is_cloud_placeholder", lambda path, **kw: True
    )
    extracted: list[int] = []

    async def fake_extract(self, f):
        extracted.append(f.id)
        return None

    monkeypatch.setattr(PipelineService, "_extract_file", fake_extract)

    await PipelineService(session).process_pending_files(job.id)

    await session.refresh(file)
    assert file.is_placeholder is True
    assert file.status == "deferred"
    assert extracted == []


async def test_flagged_placeholder_is_not_selected(
    session: AsyncSession, tmp_path
):
    """Instances already known to be dataless are not even queued."""
    _, job, file = await _setup(session, tmp_path)
    file.is_placeholder = True
    await session.commit()

    await PipelineService(session).process_pending_files(job.id)

    await session.refresh(file)
    await session.refresh(job)
    assert file.status == "pending"  # untouched, not queued
    assert job.total_files == 0


# ---------------------------------------------------------------------------
# Phase 2a facts cache
# ---------------------------------------------------------------------------

class _FactsProvider:
    """Fake LLM: facts JSON for the facts pass, assignments for categorisation."""

    def __init__(self, fail_first_facts: bool = False) -> None:
        self.fail_first_facts = fail_first_facts
        self.failed = False
        self.facts_calls = 0
        self.category_calls = 0

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if "document-analysis" in system_prompt:
            self.facts_calls += 1
            if self.fail_first_facts and not self.failed:
                self.failed = True
                raise RuntimeError("facts boom")
            return {
                "summary": "A summary.",
                "doc_type": "note",
                "topics": ["t"],
                "entities": {},
            }
        self.category_calls += 1
        return {"files": []}


async def _make_source(session, tmp_path, *, store_facts: bool = True) -> Source:
    source = Source(path=str(tmp_path), recursive=True, store_facts=store_facts)
    session.add(source)
    await session.flush()
    return source


async def _add_hashed_file(session, source, tmp_path, name, digest):
    target = tmp_path / name
    target.write_text("content of " + name)
    doc = Document(content_hash=digest, status="pending")
    session.add(doc)
    await session.flush()
    file = File(
        source_id=source.id,
        document_id=doc.id,
        content_hash=digest,
        path=str(target),
        filename=name,
        extension=".txt",
        size_bytes=target.stat().st_size,
        status="pending",
    )
    session.add(file)
    await session.flush()
    return file, doc


def _patch_facts_run(monkeypatch, provider, extractions):
    async def fake_provider(self, kind="chat"):
        return provider if kind == "chat" else None

    async def fake_extract(self, file):
        extractions.append(file.id)
        return ExtractionResult(text="content", metadata={}, char_count=7)

    monkeypatch.setattr(PipelineService, "_get_active_provider", fake_provider)
    monkeypatch.setattr(PipelineService, "_extract_file", fake_extract)


async def test_facts_built_once_then_reused(
    session: AsyncSession, tmp_path, monkeypatch
):
    source = await _make_source(session, tmp_path)
    file, doc = await _add_hashed_file(session, source, tmp_path, "a.txt", "sha256:aaa")
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add(job)
    await session.commit()

    provider = _FactsProvider()
    extractions: list[int] = []
    _patch_facts_run(monkeypatch, provider, extractions)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id)

    assert provider.facts_calls == 1
    assert extractions == [file.id]
    fact = await ContentFactRepository.get(session, "sha256:aaa")
    assert fact is not None and fact.summary == "A summary."
    assert fact.facts_version == 1
    await session.refresh(doc)
    assert doc.status == "extracted"  # no assignments returned

    # Second run: facts are fresh -> no extraction, no facts LLM call.
    await pipeline.process_pending_files(job.id)
    assert provider.facts_calls == 1
    assert extractions == [file.id]
    assert provider.category_calls == 2


async def test_facts_failure_is_isolated_and_retried(
    session: AsyncSession, tmp_path, monkeypatch
):
    source = await _make_source(session, tmp_path)
    file_a, _ = await _add_hashed_file(session, source, tmp_path, "a.txt", "sha256:aaa")
    file_b, _ = await _add_hashed_file(session, source, tmp_path, "b.txt", "sha256:bbb")
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add(job)
    await session.commit()

    provider = _FactsProvider(fail_first_facts=True)
    extractions: list[int] = []
    _patch_facts_run(monkeypatch, provider, extractions)
    categorize_calls: list[list[int]] = []

    async def fake_categorize(self, files, extracted_texts, **kwargs):
        categorize_calls.append([f.id for f in files])
        return {f.id for f in files}

    monkeypatch.setattr(PipelineService, "_categorize_batch", fake_categorize)

    pipeline = PipelineService(session)
    await pipeline.process_pending_files(job.id, batch_size=1)

    await session.refresh(file_a)
    await session.refresh(file_b)
    await session.refresh(job)
    assert file_a.status == "extracted"  # retried, not failed outright
    assert file_b.status == "categorized"
    assert categorize_calls == [[file_b.id]]  # A excluded from this batch
    assert job.error_count == 1
    assert "[facts]" in (job.error_log or "")

    # Next run retries A and succeeds.
    await pipeline.process_pending_files(job.id, batch_size=1)
    assert await ContentFactRepository.get(session, "sha256:aaa") is not None


async def test_store_facts_off_keeps_ephemeral_path(
    session: AsyncSession, tmp_path, monkeypatch
):
    source = await _make_source(session, tmp_path, store_facts=False)
    file, doc = await _add_hashed_file(session, source, tmp_path, "a.txt", "sha256:aaa")
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add(job)
    await session.commit()

    provider = _FactsProvider()
    extractions: list[int] = []
    _patch_facts_run(monkeypatch, provider, extractions)

    async def fake_categorize(self, files, extracted_texts, **kwargs):
        assert file.id in extracted_texts  # snippet path was used
        return {f.id for f in files}

    monkeypatch.setattr(PipelineService, "_categorize_batch", fake_categorize)

    await PipelineService(session).process_pending_files(job.id)

    facts = (await session.execute(select(ContentFact))).scalars().all()
    assert facts == []
    assert provider.facts_calls == 0
    assert extractions == [file.id]
    await session.refresh(doc)
    assert doc.status == "categorized"


class _EmbeddingProvider:
    """Fake embedding provider with a settable model name."""

    def __init__(self, model_name: str = "test-embed", fail: bool = False) -> None:
        self.model_name = model_name
        self.fail = fail
        self.embed_calls = 0
        self.texts: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        if self.fail:
            raise RuntimeError("embed boom")
        self.texts.extend(texts)
        return [[1.0, 2.0, 3.0] for _ in texts]


async def _with_embeddings(
    session, tmp_path, monkeypatch, embed_provider: _EmbeddingProvider
):
    source = await _make_source(session, tmp_path)
    file, doc = await _add_hashed_file(
        session, source, tmp_path, "a.txt", "sha256:emb"
    )
    job = Job(job_type="quick_sync", source_id=source.id)
    session.add(job)
    await session.commit()

    chat = _FactsProvider()
    extractions: list[int] = []

    async def fake_provider(self, kind="chat"):
        return embed_provider if kind == "embedding" else chat

    async def fake_extract(self, f):
        extractions.append(f.id)
        return ExtractionResult(text="content", metadata={}, char_count=7)

    monkeypatch.setattr(PipelineService, "_get_active_provider", fake_provider)
    monkeypatch.setattr(PipelineService, "_extract_file", fake_extract)
    return file, doc, job, chat, extractions


async def test_embedding_built_then_rebuilt_only_on_model_change(
    session: AsyncSession, tmp_path, monkeypatch
):
    from cabinet.utils.embeddings import unpack_embedding

    embed = _EmbeddingProvider()
    file, _doc, job, chat, extractions = await _with_embeddings(
        session, tmp_path, monkeypatch, embed
    )
    pipeline = PipelineService(session)

    await pipeline.process_pending_files(job.id)
    fact = await ContentFactRepository.get(session, "sha256:emb")
    assert fact is not None
    assert fact.embedding is not None
    assert fact.embedding_model == "test-embed"
    assert fact.embedding_dim == 3
    assert unpack_embedding(fact.embedding) == [1.0, 2.0, 3.0]
    assert embed.embed_calls == 1
    assert chat.facts_calls == 1
    assert extractions == [file.id]

    # Fresh fact + fresh vector: nothing recomputed.
    await pipeline.process_pending_files(job.id)
    assert embed.embed_calls == 1
    assert chat.facts_calls == 1
    assert extractions == [file.id]

    # A model change invalidates only the vector; facts/extraction are reused.
    embed.model_name = "test-embed-v2"
    await pipeline.process_pending_files(job.id)
    assert embed.embed_calls == 2
    assert chat.facts_calls == 1
    assert extractions == [file.id]
    fact = await ContentFactRepository.get(session, "sha256:emb")
    assert fact is not None and fact.embedding_model == "test-embed-v2"


async def test_embedding_failure_keeps_facts_and_categorisation(
    session: AsyncSession, tmp_path, monkeypatch
):
    embed = _EmbeddingProvider(fail=True)
    file, doc, job, _chat, _extractions = await _with_embeddings(
        session, tmp_path, monkeypatch, embed
    )

    async def fake_categorize(self, files, extracted_texts, **kwargs):
        return {f.id for f in files}

    monkeypatch.setattr(PipelineService, "_categorize_batch", fake_categorize)

    await PipelineService(session).process_pending_files(job.id)

    fact = await ContentFactRepository.get(session, "sha256:emb")
    assert fact is not None and fact.summary == "A summary."
    assert fact.embedding is None
    await session.refresh(doc)
    assert doc.status == "categorized"  # embeddings are an enhancement
    assert embed.embed_calls == 1
