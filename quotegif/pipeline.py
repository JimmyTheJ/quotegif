from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from quotegif import timing as t
from quotegif import verbose as v
from quotegif.config import AppConfig
from quotegif.matcher import (
    _DEFAULT_THRESHOLD,
    _MIN_TOKEN_COVERAGE,
    _MIN_WORDS_FOR_COVERAGE,
    best_quote_score,
    match_quote,
    top_quote_matches,
)
from quotegif.models import ClipSpec, EpisodeRef, SubCue
from quotegif.subtitles import get_cue_source, get_cues

OutputFormat = Literal["clip", "gif"]


@dataclass
class LocateResult:
    """Result of locating a quote in a media file."""

    media_path: Path
    spec: ClipSpec
    matched_cue: SubCue
    subtitle_cues: list[SubCue]  # full-file cues (for GIF burn-in)
    match_score: float | None = None
    match_query: str | None = None
    transcript_source: str | None = None  # subtitles | whisper
    runner_up_score: float | None = None


def _log_match_rankings(query: str, cues: list[SubCue], *, label: str) -> None:
    if not v.is_verbose():
        return
    v.log(
        f"{label} — top fuzzy matches for [italic]{query!r}[/italic] "
        f"(score ≥{_DEFAULT_THRESHOLD:.0f}, coverage ≥{_MIN_TOKEN_COVERAGE:.0%} when 3+ words):"
    )
    from rich.table import Table

    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("#", justify="right", width=3)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Cover", justify="right", width=6)
    table.add_column("Time", width=16)
    table.add_column("Cue text", overflow="fold")

    for i, (score, coverage, cue) in enumerate(top_quote_matches(query, cues, top_n=10), 1):
        ok = score >= _DEFAULT_THRESHOLD and (
            len(query.split()) < _MIN_WORDS_FOR_COVERAGE
            or coverage >= _MIN_TOKEN_COVERAGE
        )
        score_style = "green" if ok and i == 1 else "yellow" if ok else "red"
        table.add_row(
            str(i),
            f"[{score_style}]{score:.0f}[/{score_style}]",
            f"{coverage:.0%}",
            f"{cue.start:.1f}s – {cue.end:.1f}s",
            cue.text[:120] + ("…" if len(cue.text) > 120 else ""),
        )
    v.log_table(table)


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
    queries: list[str] = []
    seen: set[str] = set()
    for q in [quote, ref.exact_quote]:
        if q and q.strip().lower() not in seen:
            seen.add(q.strip().lower())
            queries.append(q)

    if v.is_verbose():
        v.section("Quote location")
        v.log(f"File: {media_path}")
        v.log(f"Search queries: {queries!r}")

    with t.track_step("Load subtitles"):
        subtitle_cues = get_cues(media_path)
    transcript_source = "subtitles" if subtitle_cues else "none"
    cue_source = get_cue_source(media_path)

    if v.is_verbose():
        v.log(f"Subtitle source: {cue_source}")
        v.log(f"Loaded {len(subtitle_cues)} subtitle cues")

    best_cue: SubCue | None = None
    match_query: str | None = None
    match_score: float | None = None
    runner_up_score: float | None = None

    for q in queries:
        if not subtitle_cues:
            break
        with t.track_step("Match quote (subtitles)", detail=cue_source):
            _log_match_rankings(q, subtitle_cues, label="Subtitles")
            cue = match_quote(q, subtitle_cues)
            score, _ = best_quote_score(q, subtitle_cues)
        if cue is not None:
            best_cue = cue
            match_query = q
            match_score = score
            tops = top_quote_matches(q, subtitle_cues, top_n=2)
            if len(tops) > 1:
                runner_up_score = tops[1][0]
            break

    if best_cue is None and cfg.whisper.enabled:
        if v.is_verbose():
            v.log("No subtitle match — falling back to Whisper (full episode)")
        from quotegif.transcribe import transcribe

        whisper_detail = f"{cfg.whisper.model} / {cfg.whisper.device}"
        with t.track_step("Whisper transcription", detail=whisper_detail):
            whisper_cues = transcribe(media_path, cfg.whisper.model, cfg.whisper.device)
        subtitle_cues = whisper_cues
        transcript_source = f"whisper ({cfg.whisper.model})"
        if v.is_verbose():
            v.log(f"Whisper produced {len(whisper_cues)} segments")

        for q in queries:
            with t.track_step("Match quote (Whisper)"):
                _log_match_rankings(q, whisper_cues, label="Whisper")
                cue = match_quote(q, whisper_cues)
                score, _ = best_quote_score(q, whisper_cues)
            if cue is not None:
                best_cue = cue
                match_query = q
                match_score = score
                tops = top_quote_matches(q, whisper_cues, top_n=2)
                if len(tops) > 1:
                    runner_up_score = tops[1][0]
                break

    if best_cue is None:
        raise LookupError(
            "Could not locate the quote in the episode. "
            "Try a more specific quote or check the episode identifier."
        )

    if v.is_verbose():
        v.log(
            f"Selected: score {match_score:.0f} via {transcript_source} "
            f"(query {match_query!r})"
        )
        if runner_up_score is not None:
            gap = (match_score or 0) - runner_up_score
            v.log(f"Runner-up score: {runner_up_score:.0f} (margin {gap:.0f})")

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
        match_score=match_score,
        match_query=match_query,
        transcript_source=transcript_source,
        runner_up_score=runner_up_score,
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
