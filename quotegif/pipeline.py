from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from quotegif.config import AppConfig
from quotegif.models import ClipSpec, EpisodeRef, SubCue

OutputFormat = Literal["clip", "gif"]


@dataclass
class LocateResult:
    """Result of locating a quote in a media file."""

    media_path: Path
    spec: ClipSpec
    matched_cue: SubCue
    subtitle_cues: list[SubCue]  # full-file cues (for GIF burn-in)


def locate_quote(
    ref: EpisodeRef,
    quote: str,
    cfg: AppConfig,
    media_path: Path,
) -> LocateResult:
    """
    Find the quote timestamp in media_path using subtitles (Whisper fallback).
    Returns clip spec plus full subtitle track for rendering.
    """
    from quotegif.matcher import match_quote
    from quotegif.subtitles import get_cues

    search_query = ref.exact_quote or quote
    subtitle_cues = get_cues(media_path)
    best_cue = match_quote(search_query, subtitle_cues) if subtitle_cues else None

    if best_cue is None and cfg.whisper.enabled:
        from quotegif.transcribe import transcribe

        whisper_cues = transcribe(media_path, cfg.whisper.model, cfg.whisper.device)
        subtitle_cues = whisper_cues
        best_cue = match_quote(search_query, whisper_cues)

    if best_cue is None:
        raise LookupError(
            "Could not locate the quote in the episode. "
            "Try a more specific quote or check the episode identifier."
        )

    spec = ClipSpec(
        media_path=media_path,
        cue=best_cue,
        pad_before=cfg.pad_before,
        pad_after=cfg.pad_after,
        max_duration=cfg.max_duration,
    )
    return LocateResult(
        media_path=media_path,
        spec=spec,
        matched_cue=best_cue,
        subtitle_cues=subtitle_cues,
    )


def ensure_gif_subtitles(
    locate: LocateResult,
    cfg: AppConfig,
) -> list[SubCue]:
    """
    Return clip-relative subtitle cues for GIF burn-in.
    Generates via Whisper when no subtitles exist and whisper is enabled.
    """
    from quotegif.subtitle_render import cues_in_window, shift_cues

    cues = locate.subtitle_cues
    if not cues and cfg.whisper.enabled:
        from quotegif.transcribe import transcribe

        cues = transcribe(
            locate.media_path,
            cfg.whisper.model,
            cfg.whisper.device,
        )

    if not cues:
        raise RuntimeError(
            "GIF mode requires subtitles burned into the animation. "
            "No subtitles were found and Whisper is disabled. "
            "Enable whisper in config or use --format clip instead."
        )

    clip_cues = cues_in_window(cues, locate.spec.clip_start, locate.spec.clip_end)
    if not clip_cues:
        raise RuntimeError(
            "No subtitle cues overlap the clip window. "
            "Try increasing --pad-before / --pad-after."
        )

    return shift_cues(clip_cues, locate.spec.clip_start)


def render_output(
    locate: LocateResult,
    cfg: AppConfig,
    episode_label: str,
    output_format: OutputFormat,
) -> Path:
    """Render clip or GIF based on output_format."""
    if output_format == "clip":
        from quotegif.clipmaker import make_clip

        return make_clip(
            spec=locate.spec,
            output_dir=cfg.output_dir,
            episode_label=episode_label,
        )

    from quotegif.gifmaker import make_gif

    clip_subs = ensure_gif_subtitles(locate, cfg)
    return make_gif(
        spec=locate.spec,
        output_dir=cfg.output_dir,
        fps=cfg.gif.fps,
        width=cfg.gif.width,
        episode_label=episode_label,
        subtitle_cues=clip_subs,
    )
