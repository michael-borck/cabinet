import asyncio

from .base import ExtractionResult


class TextExtractor:
    """Extracts text content from plain text and source code files."""

    supported_extensions: set[str] = {
        ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".log", ".html", ".htm", ".css", ".js", ".ts",
        ".jsx", ".tsx", ".py", ".rb", ".go", ".rs", ".java", ".c", ".cpp",
        ".h", ".hpp", ".sh", ".bash", ".zsh", ".sql", ".r", ".swift",
        ".kt", ".scala", ".lua", ".pl", ".php", ".env", ".gitignore",
        ".dockerfile", ".makefile",
    }

    async def extract(self, file_path: str, max_chars: int = 50000) -> ExtractionResult:
        """Extract text content from a plain text or source code file."""
        try:
            text = await asyncio.to_thread(self._read_file, file_path)

            full_length = len(text)
            truncated = full_length > max_chars
            if truncated:
                text = text[:max_chars]

            return ExtractionResult(
                text=text,
                metadata={"encoding": "utf-8"},
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
    def _read_file(file_path: str) -> str:
        """Read file with UTF-8, falling back to latin-1."""
        try:
            with open(file_path, encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, encoding="latin-1") as f:
                return f.read()
