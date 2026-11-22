"""Concept-name normalisation helpers for Phase 3 taxonomy governance.

Names are resolved in a fixed order (design §4): exact slug, alias, stemmed
match, embedding similarity, then a provisional concept. These helpers cover
the deterministic alias/stem steps and the text used for concept embeddings.
"""

from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^\w\s-]", flags=re.UNICODE)
_SEPARATORS = re.compile(r"[\s._-]+")


def normalize_concept(value: str) -> str:
    """Normalise a name/alias into a stable resolution key.

    Lowercases, drops punctuation, collapses whitespace/underscores/hyphens,
    and singularises the final word so "Invoices" and "invoice" agree.
    """
    text = _NON_WORD.sub(" ", value.lower())
    text = _SEPARATORS.sub(" ", text).strip()
    if not text:
        return ""
    words = text.split(" ")
    words[-1] = _singularize(words[-1])
    return " ".join(words)


def _singularize(word: str) -> str:
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ches", "shes", "xes", "zes", "ses"):
        if word.endswith(suffix):
            return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def concept_embedding_text(
    name: str, definition: str | None, aliases: list[str] | None = None
) -> str:
    """Canonical text embedded for a concept (name + definition + aliases)."""
    parts = [name.strip()]
    if definition and definition.strip():
        parts.append(definition.strip())
    if aliases:
        cleaned = [a.strip() for a in aliases if a and a.strip()]
        if cleaned:
            parts.append("Aliases: " + ", ".join(cleaned))
    return "\n".join(parts)
