"""SimHash fingerprints for near-duplicate/version grouping (design §3.4).

A 64-bit SimHash over word shingles is cheap, stable across processes
(blake2b, not Python's randomized ``hash``), and good at "same document,
minor edits". It is a *candidate* signal: the pipeline corroborates with a
normalised filename stem and same-folder proximity before grouping, because
different invoices from one supplier can otherwise look similar.
"""

from __future__ import annotations

import hashlib
import os
import re

SHINGLE_SIZE = 3
# Bound the work on huge documents; 5k tokens is plenty for version detection.
_MAX_TOKENS = 5000

_TRAILING_VERSION = re.compile(
    r"(?i)[\s._-]*(?:v|ver|version|rev|revision|final|draft|copy|edited|updated)"
    r"\s*\d*$"
)
_TRAILING_PAREN = re.compile(r"\s*[\(\[]\d+[\)\]]$")
_TRAILING_COPY = re.compile(r"(?i)\s*-\s*copy$")


def simhash64(text: str) -> int:
    """Return a 64-bit SimHash of *text*'s word 3-shingles."""
    tokens = re.findall(r"\w+", text.lower(), flags=re.UNICODE)[:_MAX_TOKENS]
    if not tokens:
        return 0
    if len(tokens) <= SHINGLE_SIZE:
        shingles = [" ".join(tokens)]
    else:
        shingles = [
            " ".join(tokens[i : i + SHINGLE_SIZE])
            for i in range(len(tokens) - SHINGLE_SIZE + 1)
        ]

    weights = [0] * 64
    for shingle in shingles:
        digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            weights[bit] += 1 if (value >> bit) & 1 else -1

    result = 0
    for bit in range(64):
        if weights[bit] > 0:
            result |= 1 << bit
    return result


def simhash_hex(text: str) -> str:
    """Stable storage form: 16 lowercase hex characters."""
    return f"{simhash64(text):016x}"


def fingerprint_similarity(a: str | int | None, b: str | int | None) -> float:
    """Similarity in [0, 1] from Hamming distance of two SimHashes."""
    if a is None or b is None:
        return 0.0
    ai = int(a, 16) if isinstance(a, str) else a
    bi = int(b, 16) if isinstance(b, str) else b
    hamming = (ai ^ bi).bit_count()
    return 1.0 - hamming / 64.0


def stem_key(filename: str) -> str:
    """Normalise a filename for version corroboration.

    Strips extensions and common trailing version tokens (``_v2``,
    ``-final``, ``(1)``, ``- copy``) and collapses separators, so
    ``report.docx``, ``report_v2.docx`` and ``Report FINAL.docx`` share a
    key. Never returns empty for a non-empty name.
    """
    stem = os.path.splitext(filename)[0].lower()
    original = stem
    previous = None
    while previous != stem:
        previous = stem
        stem = _TRAILING_VERSION.sub("", stem)
        stem = _TRAILING_PAREN.sub("", stem)
        stem = _TRAILING_COPY.sub("", stem)
    stem = re.sub(r"[\s._-]+", " ", stem).strip()
    return stem or re.sub(r"[\s._-]+", " ", original).strip()
