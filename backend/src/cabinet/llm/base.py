from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    provider_name: str
    model_name: str

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Get a text completion."""
        ...

    async def complete_json(
        self, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        """Get a JSON completion. Parses the response as JSON."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed *texts* as document-level vectors (Phase 2b).

        Providers without an embeddings API (Anthropic) raise
        ``NotImplementedError``; embedding configs never select them.
        """
        ...

    async def test_connection(self) -> bool:
        """Test if the provider is reachable and the API key is valid."""
        ...
