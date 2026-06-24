from __future__ import annotations

from quotegif.config import AppConfig
from quotegif.models import EpisodeRef
from quotegif.providers.registry import get_provider


def identify_quote(
    quote: str,
    config: AppConfig,
    provider_override: str | None = None,
    model_override: str | None = None,
    *,
    show_hint: str | None = None,
    movie: bool = False,
) -> EpisodeRef:
    """
    Ask the configured LLM provider (with web search) to identify the show/episode
    from a vague quote. Returns a structured EpisodeRef.

    When show_hint is set, the LLM narrows to that title (season/episode) instead
    of searching across all media.
    """
    provider = get_provider(config, override=provider_override, model_override=model_override)
    ref = provider.identify(quote, show_hint=show_hint, movie=movie)
    if show_hint:
        ref.title = show_hint.strip()
        ref.media_type = "movie" if movie else "tv"
    return ref
