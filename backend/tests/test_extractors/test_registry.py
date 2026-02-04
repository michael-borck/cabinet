from file_sorter.extractors.registry import get_extractor, get_supported_extensions


def test_get_extractor_for_text():
    ext = get_extractor(".txt")
    assert ext is not None
    assert ".txt" in ext.supported_extensions


def test_get_extractor_for_pdf():
    ext = get_extractor(".pdf")
    assert ext is not None
    assert ".pdf" in ext.supported_extensions


def test_get_extractor_for_unknown():
    ext = get_extractor(".xyz123")
    assert ext is None


def test_supported_extensions():
    exts = get_supported_extensions()
    assert ".txt" in exts
    assert ".py" in exts
    assert ".pdf" in exts
    assert ".docx" in exts
    assert ".xlsx" in exts
