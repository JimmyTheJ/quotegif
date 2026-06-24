from __future__ import annotations

import json

from quotegif.config import AnthropicSettings
from quotegif.models import EpisodeRef
from quotegif.providers.prompts import build_identify_input

_SYSTEM_PROMPT = """\
You are a media identification assistant. The user will give you a vague or partially-remembered quote from a TV show or movie. Use the search tool to find the exact source.

After searching, respond with ONLY a JSON object (no markdown fences) containing:
- title (string): official show or movie title
- media_type (string): "tv" or "movie"
- season (int or null)
- episode (int or null)
- episode_title (string or null)
- exact_quote (string): verbatim quote as it appears in the script
- confidence (float 0.0–1.0)
- reasoning (string)"""


def _make_tavily_tool(api_key: str) -> dict:
    return {
        "name": "web_search",
        "description": "Search the web for information about TV shows, movies, and quotes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    }


def _run_tavily_search(query: str, api_key: str) -> str:
    try:
        from tavily import TavilyClient
    except ImportError as e:
        raise ImportError(
            "tavily-python not installed. Run: pip install 'quotegif[anthropic]'"
        ) from e

    client = TavilyClient(api_key=api_key)
    result = client.search(query, max_results=5)
    snippets = [r.get("content", "") for r in result.get("results", [])]
    return "\n\n".join(snippets[:3])


class AnthropicProvider:
    def __init__(self, settings: AnthropicSettings, model_override: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "anthropic package not installed. Run: pip install 'quotegif[anthropic]'"
            ) from e

        self._client = anthropic.Anthropic(api_key=settings.api_key)
        self._model = model_override or settings.model
        self._search_key = settings.search_api_key

    def identify(
        self,
        quote: str,
        *,
        show_hint: str | None = None,
        movie: bool = False,
    ) -> EpisodeRef:
        tools = []
        if self._search_key:
            tools.append(_make_tavily_tool(self._search_key))

        messages: list[dict] = [
            {
                "role": "user",
                "content": build_identify_input(quote, show_hint=show_hint, movie=movie),
            }
        ]

        # Agentic loop to handle tool use
        while True:
            kwargs: dict = dict(
                model=self._model,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                messages=messages,
            )
            if tools:
                kwargs["tools"] = tools

            response = self._client.messages.create(**kwargs)

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use" and block.name == "web_search":
                        search_result = _run_tavily_search(
                            block.input["query"], self._search_key
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": search_result,
                        })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
                continue

            # end_turn: extract JSON from text block
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text = block.text.strip()
                    break
            break

        if not text:
            raise ValueError("Anthropic returned an empty response.")

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
