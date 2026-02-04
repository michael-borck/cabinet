import json
import re
from typing import Any

import google.genai as genai


class GeminiProvider:
    """LLM provider implementation for Google Gemini."""

    provider_name: str = "gemini"

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
    ) -> None:
        self.model_name = model
        self.client = genai.Client(api_key=api_key)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Get a text completion from Gemini."""
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
            ),
        )
        return response.text or ""

    async def complete_json(
        self, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        """Get a JSON completion from Gemini using native JSON mime type."""
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
            ),
        )
        text = response.text or "{}"
        return self._extract_json(text)

    async def test_connection(self) -> bool:
        """Test if the Gemini API is reachable and the API key is valid."""
        try:
            await self.client.aio.models.generate_content(
                model=self.model_name,
                contents="Hi",
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=10,
                ),
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
