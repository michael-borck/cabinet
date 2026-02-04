import pytest

from file_sorter.llm.anthropic import AnthropicProvider
from file_sorter.llm.factory import create_provider
from file_sorter.llm.gemini import GeminiProvider
from file_sorter.llm.ollama import OllamaProvider
from file_sorter.llm.openai_provider import OpenAIProvider


def test_create_anthropic_provider():
    provider = create_provider("anthropic", "claude-sonnet-4-20250514", api_key="test")
    assert isinstance(provider, AnthropicProvider)
    assert provider.model_name == "claude-sonnet-4-20250514"


def test_create_openai_provider():
    provider = create_provider("openai", "gpt-4o-mini", api_key="test")
    assert isinstance(provider, OpenAIProvider)


def test_create_gemini_provider():
    provider = create_provider("gemini", "gemini-2.0-flash", api_key="test")
    assert isinstance(provider, GeminiProvider)


def test_create_ollama_provider():
    provider = create_provider("ollama", "llama3.2")
    assert isinstance(provider, OllamaProvider)


def test_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_provider("nonexistent", "model")
