from __future__ import annotations

import json

from quotegif.config import OpenAISettings
from quotegif.models import EpisodeRef

_SYSTEM_PROMPT = """\
You are a media identification assistant. The user will give you a vague or partially-remembered quote from a TV show or movie.

Use web search to find the exact source. Return ONLY a JSON object with these fields:
- title (string): official show or movie title
- media_type (string): "tv" or "movie"
- season (int or null): season number for TV shows
- episode (int or null): episode number for TV shows
- episode_title (string or null): episode title if known
- exact_quote (string): the exact verbatim quote as spoken
- confidence (float 0.0–1.0): how confident you are in this identification
- reasoning (string): brief explanation of how you identified it

If you cannot determine with reasonable confidence, set confidence below 0.5 and explain in reasoning.
Respond with ONLY the JSON object, no markdown fences."""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "media_type": {"type": "string", "enum": ["tv", "movie"]},
        "season": {"type": ["integer", "null"]},
        "episode": {"type": ["integer", "null"]},
        "episode_title": {"type": ["string", "null"]},
        "exact_quote": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["title", "media_type", "exact_quote", "confidence", "reasoning"],
    "additionalProperties": False,
}


class OpenAIProvider:
    def __init__(self, settings: OpenAISettings) -> None:
        try:
            import openai
        except ImportError as e:
            raise ImportError(
                "openai package not installed. Run: pip install 'quotegif[openai]'"
            ) from e

        self._client = openai.OpenAI(api_key=settings.api_key)
        self._model = settings.model

    def identify(self, quote: str) -> EpisodeRef:
        from openai import OpenAI  # noqa: F401 – already imported above

        response = self._client.responses.create(
            model=self._model,
            tools=[{"type": "web_search_preview"}],
            instructions=_SYSTEM_PROMPT,
            input=f'Quote: "{quote}"',
        )

        # Extract text output from the response
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

        data = json.loads(text)
        return EpisodeRef(
            title=data["title"],
            media_type=data["media_type"],
            season=data.get("season"),
            episode=data.get("episode"),
            episode_title=data.get("episode_title"),
            exact_quote=data.get("exact_quote", quote),
            confidence=float(data.get("confidence", 1.0)),
            reasoning=data.get("reasoning", ""),
        )
