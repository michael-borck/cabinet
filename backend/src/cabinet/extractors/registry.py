from .base import Extractor
from .docx import DocxExtractor
from .pdf import PDFExtractor
from .text import TextExtractor
from .xlsx import XlsxExtractor

_text_extractor = TextExtractor()
_pdf_extractor = PDFExtractor()
_docx_extractor = DocxExtractor()
_xlsx_extractor = XlsxExtractor()

_registry: dict[str, Extractor] = {}

for _extractor in (_text_extractor, _pdf_extractor, _docx_extractor, _xlsx_extractor):
    for _ext in _extractor.supported_extensions:
        _registry[_ext] = _extractor


def get_extractor(extension: str) -> Extractor | None:
    """Get the extractor for a given file extension.

    Args:
        extension: File extension including the dot (e.g. ".pdf").

    Returns:
        The matching Extractor instance, or None if unsupported.
    """
    return _registry.get(extension.lower())


def get_supported_extensions() -> set[str]:
    """Return the set of all supported file extensions."""
    return set(_registry.keys())
