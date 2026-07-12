import asyncio

from openpyxl import load_workbook

from .base import ExtractionResult


class XlsxExtractor:
    """Extracts text content from Excel files.

    Only modern OOXML workbooks: openpyxl cannot read the legacy ``.xls``
    BIFF format, so claiming it would permanently mark every ``.xls`` file
    as an error. Unclaimed extensions are categorized from metadata instead.
    """

    supported_extensions: set[str] = {".xlsx"}

    async def extract(self, file_path: str, max_chars: int = 50000) -> ExtractionResult:
        """Extract text content from an Excel file using openpyxl."""
        try:
            text, sheet_count = await asyncio.to_thread(self._extract_sync, file_path)

            full_length = len(text)
            truncated = full_length > max_chars
            if truncated:
                text = text[:max_chars]

            return ExtractionResult(
                text=text,
                metadata={"sheet_count": sheet_count},
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
        wb = load_workbook(file_path, read_only=True, data_only=True)
        sheets: list[str] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(cell) if cell is not None else "" for cell in row]
                rows.append("\t".join(cells))
            sheets.append(f"--- {sheet_name} ---\n" + "\n".join(rows))

        sheet_count = len(wb.sheetnames)
        wb.close()
        return "\n\n".join(sheets), sheet_count
