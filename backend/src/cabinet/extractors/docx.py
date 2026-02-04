import asyncio

from docx import Document

from .base import ExtractionResult


class DocxExtractor:
    """Extracts text content from DOCX files."""

    supported_extensions: set[str] = {".docx"}

    async def extract(self, file_path: str, max_chars: int = 50000) -> ExtractionResult:
        """Extract text content from a DOCX file using python-docx."""
        try:
            text = await asyncio.to_thread(self._extract_sync, file_path)

            full_length = len(text)
            truncated = full_length > max_chars
            if truncated:
                text = text[:max_chars]

            return ExtractionResult(
                text=text,
                metadata={},
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
    def _extract_sync(file_path: str) -> str:
        """Synchronous extraction logic to be run in a thread."""
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs]
        return "\n".join(paragraphs)
