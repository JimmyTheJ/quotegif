from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class EpisodeRef:
    """Structured result returned by an LLM provider after identifying a quote's source."""

    title: str
    media_type: Literal["tv", "movie"]
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    exact_quote: str | None = None
    approx_timestamp: float | None = None  # seconds into episode, when known
    confidence: float = 1.0  # 0.0–1.0
    reasoning: str = ""

    def display(self) -> str:
        if self.media_type == "tv" and self.season and self.episode:
            return f"{self.title} S{self.season:02d}E{self.episode:02d}"
        return self.title


@dataclass
class SubCue:
    """A single subtitle cue parsed from an .srt or Whisper transcription."""

    start: float   # seconds
    end: float     # seconds
    text: str
    index: int = 0


@dataclass
class ClipSpec:
    """Fully resolved clip: file path + time window for GIF extraction."""

    media_path: Path
    cue: SubCue
    pad_before: float
    pad_after: float
    max_duration: float

    @property
    def clip_start(self) -> float:
        return max(0.0, self.cue.start - self.pad_before)

    @property
    def clip_end(self) -> float:
        raw_end = self.cue.end + self.pad_after
        capped_end = self.clip_start + self.max_duration
        return min(raw_end, capped_end)

    @property
    def duration(self) -> float:
        return self.clip_end - self.clip_start


@dataclass
class MediaEntry:
    """A video file in the library index."""

    path: Path
    title: str
    media_type: Literal["tv", "movie"]
    season: int | None = None
    episode: int | None = None
    year: int | None = None
    raw_guess: dict = field(default_factory=dict)
