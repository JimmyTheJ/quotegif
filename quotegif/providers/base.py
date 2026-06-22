from __future__ import annotations

from typing import Protocol, runtime_checkable

from quotegif.models import EpisodeRef


@runtime_checkable
class Provider(Protocol):
    """Contract every LLM provider must satisfy."""

    def identify(self, quote: str) -> EpisodeRef:
        """
        Given a vague quote string, use web search (if available) and the
        model's knowledge to identify the show/movie, season, and episode,
        returning a structured EpisodeRef.
        """
        ...
