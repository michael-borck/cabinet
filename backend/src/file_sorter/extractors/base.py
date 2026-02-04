from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ExtractionResult:
    text: str
    metadata: dict[str, str | int | float | None]
    char_count: int
    truncated: bool = False


@runtime_checkable
class Extractor(Protocol):
    """Protocol for file content extractors."""

    supported_extensions: set[str]

    async def extract(self, file_path: str, max_chars: int = 50000) -> ExtractionResult:
        """Extract text content from a file."""
        ...
