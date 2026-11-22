"""Processing pipeline: text extraction and LLM categorization."""

from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.config import get_settings
from cabinet.db.models import Document, File, Job, LLMConfig, Source
from cabinet.db.repositories import ContentFactRepository, DocumentRepository
from cabinet.extractors.base import ExtractionResult
from cabinet.extractors.registry import EXTRACTOR_VERSION, get_extractor
from cabinet.llm.base import LLMProvider
from cabinet.llm.factory import create_provider
from cabinet.services.embeddings import EmbeddingService, build_embedding_text
from cabinet.services.facts import FACTS_VERSION, FactsService
from cabinet.utils.cloud import is_cloud_placeholder
from cabinet.utils.embeddings import pack_embedding

logger = logging.getLogger(__name__)


def _queue_embedding(
    targets: list[tuple[object, str]],
    fact: object,
    service: EmbeddingService | None,
) -> None:
    """Queue *fact* for (re)embedding when its vector is stale or missing."""
    if service is None or fact is None:
        return
    if ContentFactRepository.is_embedding_fresh(fact, model=service.model):
        return
    text = build_embedding_text(
        getattr(fact, "summary", None), getattr(fact, "topics", None)
    )
    if text:
        targets.append((fact, text))


class PipelineService:
    """Orchestrates the file-processing pipeline (extraction + categorization)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_pending_files(
        self, job_id: int, batch_size: int | None = None
    ) -> None:
        """Process all pending files associated with a job's source.

        Files are processed in batches:
        1. Extract text content from each file.
        2. Optionally categorize via LLM (if an active config exists).

        Args:
            job_id: The ID of the :class:`Job` tracking this run.
            batch_size: Number of files per batch.  Falls back to the
                ``categorization_batch_size`` setting.
        """
        if batch_size is None:
            batch_size = self._settings.categorization_batch_size

        # Load the job --------------------------------------------------------
        job = await self._session.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job with id={job_id} not found")
        if job.status == "cancelled":
            logger.info("Job %d already cancelled; skipping pipeline", job_id)
            return

        # Facts are opt-in per source (design §5.4, D3). Without an active
        # provider we cannot build them and fall back to ephemeral snippets.
        source = (
            await self._session.get(Source, job.source_id)
            if job.source_id is not None
            else None
        )
        facts_enabled = bool(source and source.store_facts)
        provider = await self._get_active_provider()
        embedding_provider = await self._get_active_provider("embedding")
        embedding_service = (
            EmbeddingService(embedding_provider, embedding_provider.model_name)
            if embedding_provider is not None
            else None
        )

        # Determine which files to process ------------------------------------
        # "extracted" files are retried: their previous run failed or skipped
        # categorisation. With fresh Tier-1 facts they are re-categorised
        # without re-extraction; otherwise text is ephemeral and they extract
        # again.
        # Only ids are held for the whole run; each batch re-fetches its File
        # rows. A rollback expires every instance in the session, and touching
        # an expired attribute on an AsyncSession raises MissingGreenlet — so
        # instances must not outlive the batch they belong to.
        query = (
            select(File.id, File.document_id)
            .where(
                File.source_id == job.source_id,
                File.status.in_(("pending", "extracted")),
                # Cloud placeholders are deferred, not queued (design §4.2/D4).
                File.is_placeholder == False,  # noqa: E712
            )
            .order_by(File.id)
        )
        result = await self._session.execute(query)
        # One representative instance per document (design §3.1): a document
        # is extracted/categorised once, and its status mirrors to every
        # instance. Files without a document yet each stand for themselves.
        pending_ids: list[int] = []
        seen_documents: set[int] = set()
        for file_id, document_id in result.all():
            if document_id is not None:
                if document_id in seen_documents:
                    continue
                seen_documents.add(document_id)
            pending_ids.append(file_id)

        job.total_files = len(pending_ids)
        job.processed_files = 0
        job.status = "running"
        # Commit per batch (not one run-long transaction): progress becomes
        # visible to other sessions, the SQLite write lock is released while
        # the LLM call is in flight, and a crash loses one batch, not the run.
        await self._session.commit()

        cancelled = False
        for batch_start in range(0, len(pending_ids), batch_size):
            if await self._job_cancelled(job):
                cancelled = True
                logger.info("Job %d cancelled; stopping pipeline", job.id)
                break

            batch_ids = pending_ids[batch_start : batch_start + batch_size]
            result = await self._session.execute(
                select(File).where(File.id.in_(batch_ids)).order_by(File.id)
            )
            # Files deleted since the run started simply drop out of the batch.
            batch: list[File] = list(result.scalars().all())

            # Guarantee documents, then drop any representatives that turned
            # out to sight the same document (legacy unlinked duplicates).
            for file in batch:
                await DocumentRepository.ensure_for_file(self._session, file)
            deduped: list[File] = []
            batch_documents: set[int] = set()
            for file in batch:
                if file.document_id in batch_documents:
                    continue
                batch_documents.add(file.document_id)  # type: ignore[arg-type]
                deduped.append(file)
            batch = deduped

            # -- Cloud-placeholder guard --------------------------------------
            # A file can dehydrate between the scan and this run; reading it
            # would silently download it. Park such instances instead.
            runnable: list[File] = []
            for file in batch:
                if await self._park_if_placeholder(file):
                    continue
                runnable.append(file)
            batch = runnable

            # -- Extraction / facts -------------------------------------------
            # Tier 1 (design §5.2): reuse fresh facts instead of extracting
            # again. Per-document, so every instance of a hash pays once.
            extracted_batch: list[File] = []
            extracted_texts: dict[int, str] = {}
            facts_map: dict[int, object] = {}
            embedding_targets: list[tuple[object, str]] = []
            for file in batch:
                try:
                    extraction: ExtractionResult | None = None
                    fact = None
                    content_hash = await self._content_hash(file)

                    if facts_enabled and provider is not None and content_hash:
                        fact = await self._fresh_fact(content_hash)

                    if fact is not None:
                        # Fresh: no extraction, no facts LLM call. A stale or
                        # missing vector is rebuilt from the stored facts.
                        _queue_embedding(embedding_targets, fact, embedding_service)
                        facts_map[file.id] = fact
                        await self._set_status(file, "extracted")
                        extracted_batch.append(file)
                        continue

                    if file.extension:
                        extraction = await self._extract_file(file)

                    if (
                        facts_enabled
                        and provider is not None
                        and content_hash
                        and extraction is not None
                        and extraction.text.strip()
                    ):
                        try:
                            await self._build_and_store_facts(
                                file, content_hash, extraction, provider
                            )
                        except Exception as exc:
                            # Facts failed: leave this document "extracted" so
                            # the next run retries it, and isolate the failure
                            # from the rest of the batch (design §5.2 step 5).
                            logger.exception(
                                "Facts extraction failed for file %d (%s)",
                                file.id,
                                file.path,
                            )
                            await self._set_status(file, "extracted")
                            job.error_count += 1
                            _append_error_log(
                                job, f"[facts] file_id={file.id}: {exc}"
                            )
                            continue

                        fact = await ContentFactRepository.get(
                            self._session, content_hash
                        )
                        _queue_embedding(embedding_targets, fact, embedding_service)
                        facts_map[file.id] = fact
                        await self._set_status(file, "extracted")
                        extracted_batch.append(file)
                        continue

                    if extraction is not None:
                        extracted_texts[file.id] = extraction.text
                    await self._set_status(file, "extracted")
                    extracted_batch.append(file)
                except Exception as exc:
                    logger.exception(
                        "Extraction failed for file %d (%s)", file.id, file.path
                    )
                    await self._set_status(file, "error")
                    job.error_count += 1
                    _append_error_log(
                        job, f"[extract] file_id={file.id}: {exc}"
                    )

            if embedding_service is not None and embedding_targets:
                await self._apply_embeddings(embedding_targets, embedding_service)

            # Persist extraction/facts outcomes before the LLM call so a
            # categorization failure can't roll them back with its own writes.
            await self._session.commit()

            # -- Categorization ------------------------------------------------
            if extracted_batch:
                try:
                    categorized_ids = await self._categorize_batch(
                        extracted_batch,
                        extracted_texts,
                        facts_map=facts_map,
                        provider=provider,
                    )
                except Exception as exc:
                    logger.exception(
                        "Categorization failed for batch starting at index %d",
                        batch_start,
                    )
                    # The session may hold partial writes from the failed
                    # batch; discard them and re-record the error on the job.
                    # This batch's File instances are expired by the rollback,
                    # but they're not touched again — the files stay
                    # "extracted" (committed above) and retry on the next run.
                    await self._session.rollback()
                    await self._session.refresh(job)
                    job.error_count += 1
                    _append_error_log(
                        job,
                        f"[categorize] batch {batch_start}: {exc}",
                    )
                else:
                    if categorized_ids is not None:
                        # Only files the LLM actually returned assignments for
                        # are done; the rest stay "extracted" and are retried
                        # on the next run. categorized_ids is None when no LLM
                        # is configured -- everything stays "extracted".
                        for file in extracted_batch:
                            if file.id in categorized_ids:
                                await self._set_status(file, "categorized")

            # -- Update progress -----------------------------------------------
            job.processed_files = min(
                batch_start + len(batch_ids), len(pending_ids)
            )
            await self._session.commit()

        # Finalize job (re-check so a cancel during the last batch sticks) ----
        if not cancelled and not await self._job_cancelled(job):
            job.status = (
                "completed" if job.error_count == 0 else "completed_with_errors"
            )
            await self._session.commit()

        logger.info(
            "Pipeline finished for job %d: processed=%d, errors=%d%s",
            job.id,
            job.processed_files,
            job.error_count,
            " (cancelled)" if cancelled else "",
        )

    async def _set_status(self, file: File, status: str) -> None:
        """Set pipeline status at document level, mirrored to instances."""
        if file.document_id is None:
            file.status = status
            return
        doc = await self._session.get(Document, file.document_id)
        if doc is None:
            file.status = status
            return
        await DocumentRepository.set_status(self._session, doc, status)
        # set_status skips "missing" instances; the representative is live
        # by construction, but set it explicitly so the in-memory object
        # the rest of the loop touches is never stale.
        file.status = status

    async def _job_cancelled(self, job: Job) -> bool:
        """Re-read the job's status so a cancel from another session is seen."""
        await self._session.refresh(job, attribute_names=["status"])
        return job.status == "cancelled"

    # ------------------------------------------------------------------
    # Facts (Phase 2a)
    # ------------------------------------------------------------------

    async def _get_active_provider(
        self, kind: str = "chat"
    ) -> LLMProvider | None:
        """Build the active provider for *kind* once per run, or None."""
        result = await self._session.execute(
            select(LLMConfig)
            .where(
                LLMConfig.kind == kind,
                LLMConfig.is_active == True,  # noqa: E712
            )
            .limit(1)
        )
        config = result.scalar_one_or_none()
        if config is None:
            return None
        return create_provider(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def _apply_embeddings(
        self,
        targets: list[tuple[object, str]],
        service: EmbeddingService,
    ) -> None:
        """Embed a batch of facts and attach vectors to their rows.

        Embeddings are an enhancement: a failure here is logged and leaves the
        facts usable, rather than failing the documents.
        """
        try:
            vectors = await service.embed_texts([text for _, text in targets])
        except Exception:
            logger.exception("Embedding batch failed; facts kept without vectors")
            return

        for (fact, _text), vector in zip(targets, vectors, strict=True):
            if not vector:
                continue
            await ContentFactRepository.update_embedding(
                self._session,
                content_hash=fact.content_hash,  # type: ignore[attr-defined]
                embedding=pack_embedding(vector),
                model=service.model,
                dim=len(vector),
            )

    async def _content_hash(self, file: File) -> str | None:
        """The instance's mirrored hash, else its document's hash."""
        if file.content_hash:
            return file.content_hash
        if file.document_id is None:
            return None
        doc = await self._session.get(Document, file.document_id)
        return doc.content_hash if doc is not None else None

    async def _fresh_fact(self, content_hash: str):
        """Return a reusable fact row, or None when missing/stale."""
        fact = await ContentFactRepository.get(self._session, content_hash)
        if ContentFactRepository.is_fresh(
            fact,
            extractor_version=EXTRACTOR_VERSION,
            facts_version=FACTS_VERSION,
        ):
            return fact
        return None

    async def _build_and_store_facts(
        self,
        file: File,
        content_hash: str,
        extraction: ExtractionResult,
        provider: LLMProvider,
    ) -> None:
        """Run the facts pass and cache it; raises for the caller to isolate."""
        service = FactsService(
            provider,
            input_max_chars=self._settings.facts_input_max_chars,
        )
        result = await service.build_facts(
            filename=file.filename,
            extension=file.extension,
            text=extraction.text,
            metadata=extraction.metadata,
        )
        await ContentFactRepository.upsert(
            self._session,
            content_hash=content_hash,
            extractor_version=EXTRACTOR_VERSION,
            facts_version=FACTS_VERSION,
            summary=result.summary,
            doc_type=result.doc_type,
            language=result.language,
            entities=result.entities or None,
            topics=result.topics or None,
            provenance=result.provenance,
            char_count=result.char_count,
            page_count=result.provenance.get("page_count"),
        )

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    async def _park_if_placeholder(self, file: File) -> bool:
        """Return True and defer *file* when its bytes are cloud-only.

        Uses metadata only (no read), so it cannot trigger a download. A
        failed stat falls through to extraction, which reports the real
        error; only an explicit placeholder flag parks the file.
        """
        try:
            st = os.stat(file.path)
        except OSError:
            return False
        placeholder = is_cloud_placeholder(file.path, st=st)
        file.is_placeholder = placeholder
        if not placeholder:
            return False
        file.status = "deferred"
        return True

    async def _extract_file(self, file: File) -> ExtractionResult | None:
        """Extract text from a single file.

        Returns:
            An :class:`ExtractionResult`, or *None* when no suitable
            extractor exists for the file's extension.
        """
        if file.extension is None:
            return None

        extractor = get_extractor(file.extension)
        if extractor is None:
            logger.debug(
                "No extractor for extension %r (file %d)", file.extension, file.id
            )
            return None

        result = await extractor.extract(
            file.path, max_chars=self._settings.extraction_max_chars
        )
        # Extractors report failures via metadata rather than raising; surface
        # them so the file is marked "error" and the job's error count is real,
        # instead of categorizing an unreadable file from its filename alone.
        error = result.metadata.get("error")
        if error:
            raise RuntimeError(f"extraction failed: {error}")
        return result

    # ------------------------------------------------------------------
    # Categorization
    # ------------------------------------------------------------------

    async def _categorize_batch(
        self,
        files: list[File],
        extracted_texts: dict[int, str],
        *,
        facts_map: dict[int, object] | None = None,
        provider: LLMProvider | None = None,
    ) -> set[int] | None:
        """Categorize a batch of files using the active LLM config.

        Returns:
            The ids of files that received at least one category assignment,
            or *None* when no active LLM configuration exists (files then stay
            in the ``"extracted"`` state).
        """
        if provider is None:
            provider = await self._get_active_provider()
        if provider is None:
            logger.debug("No active LLM configuration; skipping categorization")
            return None

        # Import here to avoid a module cycle with CategorizationService.
        from cabinet.services.categorization import CategorizationService

        categorizer = CategorizationService(self._session, provider)
        return await categorizer.categorize_files(
            files, extracted_texts=extracted_texts, facts=facts_map
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _append_error_log(job: Job, message: str) -> None:
    """Append a line to the job's ``error_log`` field."""
    if job.error_log is None:
        job.error_log = message
    else:
        job.error_log += "\n" + message
