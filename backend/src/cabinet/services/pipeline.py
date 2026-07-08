"""Processing pipeline: text extraction and LLM categorization."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.config import get_settings
from cabinet.db.models import File, Job, LLMConfig
from cabinet.extractors.base import ExtractionResult
from cabinet.extractors.registry import get_extractor
from cabinet.llm.factory import create_provider

logger = logging.getLogger(__name__)


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

        # Determine which files to process ------------------------------------
        # "extracted" files are retried: they were extracted in a previous run
        # whose categorization failed or skipped them (text is ephemeral, so
        # they go through extraction again).
        query = (
            select(File)
            .where(
                File.source_id == job.source_id,
                File.status.in_(("pending", "extracted")),
            )
            .order_by(File.id)
        )
        result = await self._session.execute(query)
        pending_files: list[File] = list(result.scalars().all())

        job.total_files = len(pending_files)
        job.processed_files = 0
        job.status = "running"
        # Commit per batch (not one run-long transaction): progress becomes
        # visible to other sessions, the SQLite write lock is released while
        # the LLM call is in flight, and a crash loses one batch, not the run.
        await self._session.commit()

        cancelled = False
        for batch_start in range(0, len(pending_files), batch_size):
            if await self._job_cancelled(job):
                cancelled = True
                logger.info("Job %d cancelled; stopping pipeline", job.id)
                break

            batch = pending_files[batch_start : batch_start + batch_size]

            # -- Extraction ---------------------------------------------------
            extracted_batch: list[File] = []
            extracted_texts: dict[int, str] = {}
            for file in batch:
                try:
                    extraction = await self._extract_file(file)
                    if extraction is not None:
                        extracted_texts[file.id] = extraction.text

                    file.status = "extracted"
                    extracted_batch.append(file)
                except Exception as exc:
                    logger.exception(
                        "Extraction failed for file %d (%s)", file.id, file.path
                    )
                    file.status = "error"
                    job.error_count += 1
                    _append_error_log(
                        job, f"[extract] file_id={file.id}: {exc}"
                    )

            # -- Categorization ------------------------------------------------
            if extracted_batch:
                try:
                    categorized_ids = await self._categorize_batch(
                        extracted_batch, extracted_texts
                    )
                except Exception as exc:
                    logger.exception(
                        "Categorization failed for batch starting at index %d",
                        batch_start,
                    )
                    # The session may hold partial writes from the failed
                    # batch; discard them and re-record the error on the job.
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
                                file.status = "categorized"

            # -- Update progress -----------------------------------------------
            job.processed_files = min(
                batch_start + len(batch), len(pending_files)
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

    async def _job_cancelled(self, job: Job) -> bool:
        """Re-read the job's status so a cancel from another session is seen."""
        await self._session.refresh(job, attribute_names=["status"])
        return job.status == "cancelled"

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

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
        self, files: list[File], extracted_texts: dict[int, str]
    ) -> set[int] | None:
        """Categorize a batch of files using the active LLM config.

        Returns:
            The ids of files that received at least one category assignment,
            or *None* when no active LLM configuration exists (files then stay
            in the ``"extracted"`` state).
        """
        # Find the active LLM configuration -----------------------------------
        result = await self._session.execute(
            select(LLMConfig).where(LLMConfig.is_active == True)  # noqa: E712
        )
        llm_config = result.scalar_one_or_none()
        if llm_config is None:
            logger.debug("No active LLM configuration; skipping categorization")
            return None

        # Build the provider and delegate to CategorizationService -------------
        from cabinet.services.categorization import CategorizationService

        provider = create_provider(
            provider=llm_config.provider,
            model=llm_config.model,
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        )

        categorizer = CategorizationService(self._session, provider)
        return await categorizer.categorize_files(
            files, extracted_texts=extracted_texts
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
