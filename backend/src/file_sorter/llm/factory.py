from file_sorter.llm.anthropic import AnthropicProvider
from file_sorter.llm.base import LLMProvider
from file_sorter.llm.gemini import GeminiProvider
from file_sorter.llm.ollama import OllamaProvider
from file_sorter.llm.openai_provider import OpenAIProvider

_PROVIDER_MAP: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def create_provider(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Create an LLM provider instance.

    Args:
        provider: Provider name ("anthropic", "openai", "gemini", "ollama").
        model: Model name to use.
        api_key: API key for the provider. Optional for Ollama.
        base_url: Custom base URL. Used by Ollama and OpenAI-compatible providers.

    Returns:
        An LLMProvider instance.

    Raises:
        ValueError: If the provider name is not recognized.
    """
    provider_key = provider.lower().strip()
    provider_class = _PROVIDER_MAP.get(provider_key)

    if provider_class is None:
        supported = ", ".join(sorted(_PROVIDER_MAP.keys()))
        raise ValueError(
            f"Unknown LLM provider: {provider!r}. "
            f"Supported providers: {supported}"
        )

    kwargs: dict[str, str | None] = {"model": model, "api_key": api_key}

    # Only pass base_url to providers that accept it
    if provider_key in ("openai", "ollama"):
        kwargs["base_url"] = base_url

    return provider_class(**{k: v for k, v in kwargs.items() if v is not None})
