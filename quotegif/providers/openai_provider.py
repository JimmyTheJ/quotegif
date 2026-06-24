from __future__ import annotations

import json

from quotegif.config import OpenAISettings
from quotegif.models import EpisodeRef
from quotegif.providers.prompts import build_identify_input
from quotegif.providers.response import parse_candidates, parse_json_response, system_prompt


class OpenAIProvider:
    def __init__(self, settings: OpenAISettings, model_override: str | None = None) -> None:
        try:
            import openai
        except ImportError as e:
            raise ImportError(
                "openai package not installed. Run: pip install 'quotegif[openai]'"
            ) from e

        self._client = openai.OpenAI(api_key=settings.api_key)
        self._model = model_override or settings.model

    def identify(
        self,
        quote: str,
        *,
        show_hint: str | None = None,
        movie: bool = False,
        max_candidates: int = 5,
    ) -> list[EpisodeRef]:
        response = self._client.responses.create(
            model=self._model,
            tools=[{"type": "web_search_preview"}],
            instructions=system_prompt(show_hint=show_hint),
            input=build_identify_input(quote, show_hint=show_hint, movie=movie),
        )

        text = ""
        for block in response.output:
            if hasattr(block, "content"):
                for part in block.content:
                    if hasattr(part, "text"):
                        text = part.text
                        break
            if text:
                break

        if not text:
            raise ValueError("OpenAI returned an empty response.")

        data = parse_json_response(text)
        return parse_candidates(data, quote)[:max_candidates]
