from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from quotegif.models import SubCue


def cues_in_window(cues: list[SubCue], window_start: float, window_end: float) -> list[SubCue]:
    """Return cues that overlap [window_start, window_end], sorted by start time."""
    overlapping = [c for c in cues if c.end > window_start and c.start < window_end]
    return sorted(overlapping, key=lambda c: c.start)


def shift_cues(cues: list[SubCue], offset: float) -> list[SubCue]:
    """Shift cue timestamps so window_start becomes 0 (for clip-relative SRT)."""
    shifted: list[SubCue] = []
    for i, cue in enumerate(cues):
        start = max(0.0, cue.start - offset)
        end = max(start + 0.05, cue.end - offset)
        shifted.append(SubCue(start=start, end=end, text=cue.text, index=i + 1))
    return shifted


def write_srt(cues: list[SubCue], path: Path) -> None:
    """Write cues to an SRT file for ffmpeg's subtitles filter."""
    try:
        import srt
    except ImportError as e:
        raise ImportError("srt package not installed. Run: pip install quotegif") from e

    subtitles = []
    for i, cue in enumerate(cues, 1):
        subtitles.append(
            srt.Subtitle(
                index=i,
                start=timedelta(seconds=cue.start),
                end=timedelta(seconds=cue.end),
                content=cue.text,
            )
        )
    path.write_text(srt.compose(subtitles), encoding="utf-8")


def escape_ffmpeg_path(path: Path) -> str:
    """Escape a path for use in ffmpeg filter expressions."""
    normalized = path.resolve().as_posix()
    # Colons and backslashes must be escaped in filter args.
    return re.sub(r"([:\\'])", r"\\\1", normalized)


def subtitle_filter_arg(srt_path: Path, font_size: int = 20) -> str:
    """Build the subtitles= portion of an ffmpeg -vf chain."""
    escaped = escape_ffmpeg_path(srt_path)
    style = (
        f"FontName=Arial,FontSize={font_size},"
        "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,"
        "Outline=2,Shadow=1,MarginV=24,Alignment=2"
    )
    return f"subtitles='{escaped}':force_style='{style}'"
