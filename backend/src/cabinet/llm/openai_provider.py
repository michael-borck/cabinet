import json
import re
from typing import Any

import openai


class OpenAIProvider:
    """LLM provider implementation for OpenAI."""

    provider_name: str = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model_name = model
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Get a text completion from OpenAI."""
        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""

    async def complete_json(
        self, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        """Get a JSON completion from OpenAI using native JSON mode."""
        response = await self.client.chat.completions.create(
            model=self.model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = response.choices[0].message.content or "{}"
        return self._extract_json(text)

    async def test_connection(self) -> bool:
        """Test if the OpenAI API is reachable and the API key is valid."""
        try:
            await self.client.chat.completions.create(
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
