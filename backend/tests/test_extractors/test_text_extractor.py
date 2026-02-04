import os
import tempfile

import pytest

from file_sorter.extractors.text import TextExtractor


@pytest.fixture
def extractor():
    return TextExtractor()


@pytest.fixture
def sample_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Hello, World! This is a test file.\nLine 2\nLine 3")
        path = f.name
    yield path
    os.unlink(path)


async def test_extract_text_file(extractor, sample_file):
    result = await extractor.extract(sample_file)
    assert result.text.startswith("Hello, World!")
    assert result.char_count > 0
    assert not result.truncated
    assert "error" not in result.metadata


async def test_extract_with_truncation(extractor, sample_file):
    result = await extractor.extract(sample_file, max_chars=10)
    assert len(result.text) == 10
    assert result.truncated


async def test_extract_nonexistent_file(extractor):
    result = await extractor.extract("/nonexistent/path.txt")
    assert result.text == ""
    assert result.char_count == 0
    assert "error" in result.metadata


async def test_supported_extensions(extractor):
    assert ".py" in extractor.supported_extensions
    assert ".txt" in extractor.supported_extensions
    assert ".md" in extractor.supported_extensions
    assert ".pdf" not in extractor.supported_extensions
