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


def padded_clip_length(cue: SubCue, pad_before: float, pad_after: float) -> float:
    """Seconds needed to include the full cue plus both pads."""
    cue_len = max(0.0, cue.end - cue.start)
    return pad_before + pad_after + cue_len


def resolve_effective_max_duration(
    cue: SubCue,
    pad_before: float,
    pad_after: float,
    config_max_duration: float,
    *,
    hard_cap: float | None = None,
) -> float:
    """
  The clip ceiling must be at least as long as the padded cue window.

  Default config max_duration (12s) is a safety cap for short GIFs, but it must
  not truncate an explicit pad_before/pad_after request (e.g. 60s + 15s).
    """
    padded_len = padded_clip_length(cue, pad_before, pad_after)
    effective = max(config_max_duration, padded_len)
    if hard_cap is not None and hard_cap >= 0:
        effective = min(effective, hard_cap)
    return effective


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
