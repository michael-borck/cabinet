import asyncio

import pymupdf
import pymupdf4llm

from .base import ExtractionResult


class PDFExtractor:
    """Extracts text content from PDF files."""

    supported_extensions: set[str] = {".pdf"}

    async def extract(self, file_path: str, max_chars: int = 50000) -> ExtractionResult:
        """Extract text content from a PDF file using pymupdf4llm."""
        try:
            text, page_count = await asyncio.to_thread(self._extract_sync, file_path)

            full_length = len(text)
            truncated = full_length > max_chars
            if truncated:
                text = text[:max_chars]

            return ExtractionResult(
                text=text,
                metadata={"page_count": page_count},
                char_count=len(text),
                truncated=truncated,
            )
        except Exception as e:
            return ExtractionResult(
                text="",
                metadata={"error": str(e), "error_type": type(e).__name__},
                char_count=0,
                truncated=False,
            )

    @staticmethod
    def _extract_sync(file_path: str) -> tuple[str, int]:
        """Synchronous extraction logic to be run in a thread."""
        text = pymupdf4llm.to_markdown(file_path)
        doc = pymupdf.open(file_path)
        page_count = len(doc)
        doc.close()
        return text, page_count
