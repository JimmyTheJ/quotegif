from __future__ import annotations

from quotegif.config import AppConfig
from quotegif.models import EpisodeRef
from quotegif.providers.registry import get_provider


def identify_quote_candidates(
    quote: str,
    config: AppConfig,
    provider_override: str | None = None,
    model_override: str | None = None,
    *,
    show_hint: str | None = None,
    movie: bool = False,
    max_candidates: int = 5,
) -> list[EpisodeRef]:
    """
    Ask the LLM for ranked episode identification candidates.

    When show_hint is set, candidates are scoped to that show/movie.
    """
    provider = get_provider(config, override=provider_override, model_override=model_override)
    refs = provider.identify(
        quote,
        show_hint=show_hint,
        movie=movie,
        max_candidates=max_candidates,
    )
    if show_hint:
        for ref in refs:
            ref.title = show_hint.strip()
            ref.media_type = "movie" if movie else "tv"
    return refs


def identify_quote(
    quote: str,
    config: AppConfig,
    provider_override: str | None = None,
    model_override: str | None = None,
    *,
    show_hint: str | None = None,
    movie: bool = False,
    max_candidates: int = 5,
) -> EpisodeRef:
    """Return the top LLM identification candidate (see identify_quote_candidates)."""
    candidates = identify_quote_candidates(
        quote,
        config,
        provider_override=provider_override,
        model_override=model_override,
        show_hint=show_hint,
        movie=movie,
        max_candidates=max_candidates,
    )
    return candidates[0]
