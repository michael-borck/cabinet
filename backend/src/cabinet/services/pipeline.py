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
        result = await self._session.execute(
            select(Job).where(Job.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job with id={job_id} not found")

        # Determine which files to process ------------------------------------
        query = (
            select(File)
            .where(File.source_id == job.source_id, File.status == "pending")
            .order_by(File.id)
        )
        result = await self._session.execute(query)
        pending_files: list[File] = list(result.scalars().all())

        job.total_files = len(pending_files)
        job.status = "running"
        await self._session.flush()

        # Process in batches --------------------------------------------------
        for batch_start in range(0, len(pending_files), batch_size):
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

            await self._session.flush()

            # -- Categorization ------------------------------------------------
            if extracted_batch:
                try:
                    await self._categorize_batch(
                        extracted_batch, extracted_texts
                    )
                    for file in extracted_batch:
                        if file.status == "extracted":
                            file.status = "categorized"
                except Exception as exc:
                    logger.exception(
                        "Categorization failed for batch starting at index %d",
                        batch_start,
                    )
                    job.error_count += 1
                    _append_error_log(
                        job,
                        f"[categorize] batch {batch_start}: {exc}",
                    )

            # -- Update progress -----------------------------------------------
            job.processed_files = min(
                batch_start + len(batch), len(pending_files)
            )
            await self._session.flush()

        # Finalize job --------------------------------------------------------
        job.status = "completed" if job.error_count == 0 else "completed_with_errors"
        await self._session.flush()

        logger.info(
            "Pipeline finished for job %d: processed=%d, errors=%d",
            job.id,
            job.processed_files,
            job.error_count,
        )

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

        return await extractor.extract(
            file.path, max_chars=self._settings.extraction_max_chars
        )

    # ------------------------------------------------------------------
    # Categorization
    # ------------------------------------------------------------------

    async def _categorize_batch(
        self, files: list[File], extracted_texts: dict[int, str]
    ) -> None:
        """Categorize a batch of files using the active LLM config.

        If no active LLM configuration is present this method silently
        returns without error, leaving files in the ``"extracted"`` state.
        """
        # Find the active LLM configuration -----------------------------------
        result = await self._session.execute(
            select(LLMConfig).where(LLMConfig.is_active == True)  # noqa: E712
        )
        llm_config = result.scalar_one_or_none()
        if llm_config is None:
            logger.debug("No active LLM configuration; skipping categorization")
            return

        # Build the provider and delegate to CategorizationService -------------
        from cabinet.services.categorization import CategorizationService

        provider = create_provider(
            provider=llm_config.provider,
            model=llm_config.model,
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        )

        categorizer = CategorizationService(self._session, provider)
        await categorizer.categorize_files(files, extracted_texts=extracted_texts)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _append_error_log(job: Job, message: str) -> None:
    """Append a line to the job's ``error_log`` field."""
    if job.error_log is None:
        job.error_log = message
    else:
        job.error_log += "\n" + message
