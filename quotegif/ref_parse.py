from __future__ import annotations

import re

from quotegif.models import EpisodeRef


def parse_episode_string(
    text: str,
    fallback_quote: str,
    *,
    show_override: str | None = None,
    movie: bool = False,
) -> EpisodeRef:
    """Parse episode hints like 'The Office S03E14' or 'S03E14' with a show name."""
    text = text.strip()

    pattern_full = r"^(.*?)\s+[Ss](\d+)[Ee](\d+)\s*$"
    m = re.match(pattern_full, text)
    if m:
        return EpisodeRef(
            title=m.group(1).strip(),
            media_type="tv",
            season=int(m.group(2)),
            episode=int(m.group(3)),
            exact_quote=fallback_quote,
            confidence=1.0,
            reasoning="Provided episode hint",
        )

    pattern_short = r"^[Ss](\d+)[Ee](\d+)\s*$"
    m = re.match(pattern_short, text)
    if m and show_override:
        return EpisodeRef(
            title=show_override.strip(),
            media_type="tv",
            season=int(m.group(1)),
            episode=int(m.group(2)),
            exact_quote=fallback_quote,
            confidence=1.0,
            reasoning="Provided show + episode hint",
        )

    if show_override:
        return EpisodeRef(
            title=show_override.strip(),
            media_type="movie" if movie else "tv",
            exact_quote=fallback_quote,
            confidence=1.0,
            reasoning="Provided show hint",
        )

    return EpisodeRef(
        title=text,
        media_type="movie",
        exact_quote=fallback_quote,
        confidence=1.0,
        reasoning="Provided episode hint",
    )


def resolve_ref_from_hints(
    quote: str,
    *,
    show: str | None = None,
    episode: str | None = None,
    movie: bool = False,
) -> EpisodeRef:
    """Build an EpisodeRef from show / episode hints without calling the LLM."""
    if episode:
        return parse_episode_string(
            episode, quote, show_override=show, movie=movie
        )
    if show and movie:
        return EpisodeRef(
            title=show.strip(),
            media_type="movie",
            exact_quote=quote,
            confidence=1.0,
            reasoning="Provided show + movie hint",
        )
    raise ValueError("episode or show+movie is required when skipping LLM identification")
