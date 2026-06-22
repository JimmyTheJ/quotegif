from __future__ import annotations

import json

from quotegif.config import OllamaSettings
from quotegif.models import EpisodeRef

_PROMPT_TEMPLATE = """\
You are a media identification assistant. A user has given you a vague or partially-remembered quote from a TV show or movie. Use your knowledge to identify the source.

Quote: "{quote}"

Respond with ONLY a JSON object (no markdown fences, no extra text) containing:
- title (string): official show or movie title
- media_type (string): "tv" or "movie"
- season (int or null)
- episode (int or null)
- episode_title (string or null)
- exact_quote (string): the verbatim quote as it is spoken
- confidence (float 0.0–1.0): how confident you are
- reasoning (string): brief explanation

Note: You do not have access to web search, so base your answer on your training knowledge only."""


class OllamaProvider:
    def __init__(self, settings: OllamaSettings) -> None:
        try:
            import ollama  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "ollama package not installed. Run: pip install 'quotegif[ollama]'"
            ) from e

        self._model = settings.model
        self._host = settings.host

    def identify(self, quote: str) -> EpisodeRef:
        import ollama

        client = ollama.Client(host=self._host)
        prompt = _PROMPT_TEMPLATE.format(quote=quote)
        response = client.generate(model=self._model, prompt=prompt)
        text = response.get("response", "").strip()

        # Strip possible markdown code fence
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines
                if not line.startswith("```")
            ).strip()

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
