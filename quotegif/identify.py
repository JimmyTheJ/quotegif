from __future__ import annotations

from quotegif.config import AppConfig
from quotegif.models import EpisodeRef
from quotegif.providers.registry import get_provider


def identify_quote(
    quote: str,
    config: AppConfig,
    provider_override: str | None = None,
) -> EpisodeRef:
    """
    Ask the configured LLM provider (with web search) to identify the show/episode
    from a vague quote. Returns a structured EpisodeRef.
    """
    provider = get_provider(config, override=provider_override)
    ref = provider.identify(quote)
    return ref
