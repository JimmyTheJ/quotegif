from __future__ import annotations

import json
import re
from typing import Any

from quotegif.models import EpisodeRef

_IDENTIFY_JSON_FIELDS = """\
Return ONLY a JSON object (no markdown fences) with:
- title (string): show or movie title
- media_type (string): "tv" or "movie"
- candidates (array of 1-5 objects, best guess first). Each candidate has:
  - season (int or null), episode (int or null), episode_title (string or null)
  - exact_quote (string): verbatim line as spoken
  - confidence (float 0.0-1.0): confidence in THIS episode assignment specifically
  - reasoning (string): what you searched; note air-order vs DVD-order if relevant

Episode numbers from fan wikis and quote sites are often wrong. The quote wording is
usually easier to verify than the episode. If unsure which episode, return several
candidates with moderate confidence — do not return one wrong episode at 95%+."""

_SHOW_SCOPED_NOTE = """\
The user has named a specific show or movie. Only list candidates from that title."""


def system_prompt(*, show_hint: str | None = None) -> str:
    lines = [
        "You are a media identification assistant. The user gives a vague or "
        "partially-remembered quote from a TV show or movie.",
        "Use web search when available to find the source.",
        _IDENTIFY_JSON_FIELDS,
    ]
    if show_hint:
        lines.append(_SHOW_SCOPED_NOTE)
    return "\n\n".join(lines)


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def parse_candidates(data: dict[str, Any], fallback_quote: str) -> list[EpisodeRef]:
    """Parse LLM JSON into ranked EpisodeRef candidates (supports legacy single-object)."""
    title = data.get("title", "")
    media_type = data.get("media_type", "tv")
    raw_candidates = data.get("candidates")

    if not raw_candidates:
        # Legacy single-object response
        raw_candidates = [data]

    refs: list[EpisodeRef] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        refs.append(
            EpisodeRef(
                title=item.get("title") or title,
                media_type=item.get("media_type") or media_type,
                season=item.get("season"),
                episode=item.get("episode"),
                episode_title=item.get("episode_title"),
                exact_quote=item.get("exact_quote") or fallback_quote,
                confidence=float(item.get("confidence", 0.5)),
                reasoning=item.get("reasoning", ""),
            )
        )
    if not refs:
        raise ValueError("LLM returned no identification candidates.")
    return refs
