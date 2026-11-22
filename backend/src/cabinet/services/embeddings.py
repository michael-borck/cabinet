"""Document-level embeddings (Phase 2b, design §5.2).

Embeddings are computed from the facts pass output (summary + topics), not
from raw full text: cheaper, more topical, and it lets an embedding be
rebuilt from an existing fact without re-extracting. One vector per document
supports organisation features (similar documents, Phase 3 concept
retrieval); it is not a RAG/retrieval layer.
"""

from __future__ import annotations

import logging

from cabinet.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def build_embedding_text(summary: str | None, topics: list[str] | None) -> str:
    """The canonical text embedded for a document."""
    parts: list[str] = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if topics:
        cleaned = [str(t).strip() for t in topics if str(t).strip()]
        if cleaned:
            parts.append("Topics: " + ", ".join(cleaned))
    return "\n\n".join(parts)


class EmbeddingService:
    """Embeds facts with a dedicated embedding provider/model."""

    def __init__(self, provider: LLMProvider, model: str) -> None:
        self._provider = provider
        self.model = model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch; raises when the provider returns a mismatched count."""
        if not texts:
            return []
        vectors = await self._provider.embed(texts)
        if len(vectors) != len(texts):
            raise ValueError(
                f"embedding provider returned {len(vectors)} vectors "
                f"for {len(texts)} inputs"
            )
        return vectors
