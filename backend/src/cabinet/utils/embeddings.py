"""Vector storage helpers for Phase 2b document embeddings.

One little-endian float32 vector per document, stored as a BLOB. At personal
scale (≤ ~100k documents) brute-force cosine is tens of milliseconds, so no
vector database or extension is introduced (design §5.2 scope guard).
"""

from __future__ import annotations

import math
import struct


def pack_embedding(vector: list[float]) -> bytes:
    """Serialize a float vector as little-endian float32 bytes."""
    if not vector:
        return b""
    return struct.pack(f"<{len(vector)}f", *(float(v) for v in vector))


def unpack_embedding(blob: bytes) -> list[float]:
    """Read a little-endian float32 BLOB back into a list of floats."""
    if not blob or len(blob) % 4 != 0:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 for empty/mismatched/zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
