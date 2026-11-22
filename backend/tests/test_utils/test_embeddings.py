"""Tests for float32 vector storage and cosine similarity (Phase 2b)."""

from cabinet.utils.embeddings import (
    cosine_similarity,
    pack_embedding,
    unpack_embedding,
)


def test_pack_unpack_roundtrip():
    vector = [0.5, -1.0, 2.25, 0.0]
    blob = pack_embedding(vector)
    assert len(blob) == 16  # four little-endian float32s
    assert unpack_embedding(blob) == vector


def test_empty_and_malformed_blobs():
    assert pack_embedding([]) == b""
    assert unpack_embedding(b"") == []
    assert unpack_embedding(b"abc") == []  # not a multiple of 4


def test_cosine_similarity():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0
    # Degenerate inputs never divide by zero.
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
