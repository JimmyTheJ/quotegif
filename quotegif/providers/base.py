from __future__ import annotations

from typing import Protocol, runtime_checkable

from quotegif.models import EpisodeRef


@runtime_checkable
class Provider(Protocol):
    """Contract every LLM provider must satisfy."""

    def identify(
        self,
        quote: str,
        *,
        show_hint: str | None = None,
        movie: bool = False,
    ) -> list[EpisodeRef]:
        """
        Given a vague quote string, use web search (if available) and the
        model's knowledge to identify the show/movie, season, and episode.

        Returns ranked candidates (best first). When show_hint is set,
        identification is scoped to that title only.
        """
        ...
