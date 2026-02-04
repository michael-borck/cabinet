import json
import re
from typing import Any

import anthropic


class AnthropicProvider:
    """LLM provider implementation for Anthropic (Claude)."""

    provider_name: str = "anthropic"

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        api_key: str | None = None,
    ) -> None:
        self.model_name = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Get a text completion from Anthropic."""
        response = await self.client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.content[0].text

    async def complete_json(
        self, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        """Get a JSON completion from Anthropic. Parses the response as JSON."""
        text = await self.complete(system_prompt, user_prompt)
        return self._extract_json(text)

    async def test_connection(self) -> bool:
        """Test if the Anthropic API is reachable and the API key is valid."""
        try:
            await self.client.messages.create(
                model=self.model_name,
                max_tokens=10,
                messages=[
                    {"role": "user", "content": "Hi"},
                ],
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from text, handling optional markdown code blocks."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from ```json ... ``` code blocks
        pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return json.loads(match.group(1).strip())

        raise ValueError(f"Could not extract valid JSON from response: {text}")
