from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from quotegif import timing as t
from quotegif import verbose as v
from quotegif.config import AppConfig, check_ffmpeg
from quotegif.episode_resolve import resolve_episode
from quotegif.library import find_media, get_index
from quotegif.media_select import select_media_file
from quotegif.models import EpisodeRef, MediaEntry
from quotegif.pipeline import LocateResult, OutputFormat, locate_quote, render_output
from quotegif.ref_parse import resolve_ref_from_hints
from quotegif.time_parse import format_timestamp, parse_timestamp

ProgressCallback = Callable[[str, str | None], None]


class FindError(Exception):
    """Base error for find workflow failures."""


class FindInputRequired(FindError):
    """Workflow paused until the user supplies a choice."""

    def __init__(
        self,
        kind: Literal["low_confidence", "file_pick"],
        message: str,
        *,
        ref: EpisodeRef | None = None,
        llm_candidates: list[EpisodeRef] | None = None,
        file_candidates: list[MediaEntry] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.ref = ref
        self.llm_candidates = llm_candidates or []
        self.file_candidates = file_candidates or []


@dataclass
class FindParams:
    quote: str
    pad_before: float | None = None
    pad_after: float | None = None
    fps: int | None = None
    width: int | None = None
    provider: str | None = None
    model: str | None = None
    show: str | None = None
    episode: str | None = None
    movie: bool = False
    candidates: int = 5
    around: str | None = None
    auto_confirm: bool = False
    output_format: OutputFormat = "gif"
    media_path: str | None = None
    llm_candidate_index: int | None = None
    resolved_ref: EpisodeRef | None = None
    cached_llm_candidates: list[EpisodeRef] | None = None


@dataclass
class MediaCandidateInfo:
    path: str
    title: str
    season: int | None
    episode: int | None
    label: str


@dataclass
class EpisodeCandidateInfo:
    display: str
    episode_title: str | None
    exact_quote: str | None
    approx_timestamp: str | None
    confidence: float
    reasoning: str
    season: int | None
    episode: int | None


@dataclass
class MatchedCueInfo:
    start: float
    end: float
    text: str
    match_score: float | None
    match_query: str | None
    transcript_source: str | None
    clip_start: float
    clip_end: float
    clip_duration: float


@dataclass
class TimingStepInfo:
    name: str
    detail: str | None
    seconds: float


@dataclass
class FindResult:
    ref: EpisodeRef
    media_path: str
    pick_reason: str
    locate: LocateResult
    output_path: str
    output_format: OutputFormat
    llm_candidates: list[EpisodeCandidateInfo] = field(default_factory=list)
    matched: MatchedCueInfo | None = None
    timings: list[TimingStepInfo] = field(default_factory=list)
    total_seconds: float = 0.0


def _episode_ref_to_info(ref: EpisodeRef) -> EpisodeCandidateInfo:
    return EpisodeCandidateInfo(
        display=ref.display(),
        episode_title=ref.episode_title,
        exact_quote=ref.exact_quote,
        approx_timestamp=(
            format_timestamp(ref.approx_timestamp) if ref.approx_timestamp else None
        ),
        confidence=ref.confidence,
        reasoning=ref.reasoning,
        season=ref.season,
        episode=ref.episode,
    )


def _media_entry_to_info(entry: MediaEntry) -> MediaCandidateInfo:
    if entry.season and entry.episode:
        label = f"S{entry.season:02d}E{entry.episode:02d}"
    else:
        label = entry.title
    return MediaCandidateInfo(
        path=str(entry.path),
        title=entry.title,
        season=entry.season,
        episode=entry.episode,
        label=label,
    )


def _matched_from_locate(locate: LocateResult) -> MatchedCueInfo:
    cue = locate.matched_cue
    spec = locate.spec
    return MatchedCueInfo(
        start=cue.start,
        end=cue.end,
        text=cue.text,
        match_score=locate.match_score,
        match_query=locate.match_query,
        transcript_source=locate.transcript_source,
        clip_start=spec.clip_start,
        clip_end=spec.clip_end,
        clip_duration=spec.duration,
    )


def apply_find_overrides(cfg: AppConfig, params: FindParams) -> AppConfig:
    """Return a copy of cfg with per-request overrides applied."""
    cfg = copy.deepcopy(cfg)
    if params.pad_before is not None:
        cfg.pad_before = params.pad_before
    if params.pad_after is not None:
        cfg.pad_after = params.pad_after
    if params.fps is not None:
        cfg.gif.fps = params.fps
    if params.width is not None:
        cfg.gif.width = params.width
    return cfg


def parse_around_seconds(around: str | None) -> float | None:
    if not around:
        return None
    return parse_timestamp(around.strip())


def run_find(
    cfg: AppConfig,
    params: FindParams,
    *,
    on_progress: ProgressCallback | None = None,
) -> FindResult:
    """
    Full find workflow (same steps as the CLI find command).

    Raises FindInputRequired when the user must confirm low confidence or pick a file.
    Raises FindError for other failures.
    """
    def progress(step: str, detail: str | None = None) -> None:
        if on_progress:
            on_progress(step, detail)

    ok, ffmpeg_msg = check_ffmpeg()
    if not ok:
        raise FindError(ffmpeg_msg)

    if not cfg.media_folders:
        raise FindError(
            "No media_folders configured. Add them to your config file or QUOTEGIF_MEDIA_FOLDERS."
        )

    cfg = apply_find_overrides(cfg, params)
    around_seconds = parse_around_seconds(params.around)
    timer = t.RunTimer()
    t.set_active_timer(timer)

    ref: EpisodeRef
    llm_candidates: list[EpisodeRef] = []
    resolved_media_path: Path | None = None
    resolved_pick_reason: str | None = None
    llm_candidate_infos: list[EpisodeCandidateInfo] = []

    skip_llm = bool(params.episode or (params.show and params.movie))

    if params.cached_llm_candidates:
        progress("identify", "Using cached LLM identification")
        llm_candidates = params.cached_llm_candidates
        llm_candidate_infos = [_episode_ref_to_info(c) for c in llm_candidates]
        if params.llm_candidate_index is not None:
            idx = params.llm_candidate_index
            if idx < 0 or idx >= len(llm_candidates):
                raise FindError(f"Invalid LLM candidate index: {idx}")
            ref = llm_candidates[idx]
        else:
            ref = params.resolved_ref or llm_candidates[0]
    elif skip_llm:
        progress("identify", "Using provided episode/movie")
        with timer.track("Use provided episode/movie"):
            try:
                ref = resolve_ref_from_hints(
                    params.quote,
                    show=params.show,
                    episode=params.episode,
                    movie=params.movie,
                )
            except ValueError as e:
                raise FindError(str(e)) from e
            llm_candidates = [ref]
            llm_candidate_infos = [_episode_ref_to_info(ref)]
    else:
        from quotegif.identify import identify_quote_candidates
        from quotegif.providers.registry import get_active_model

        provider_name = params.provider or cfg.provider.name
        model_label = get_active_model(cfg, provider_name, params.model)
        detail = f"{provider_name} / {model_label}"
        if params.show:
            progress("identify", f"Finding episode in {params.show} · {detail}")
        else:
            progress("identify", detail)

        with timer.track("LLM identification", detail=detail):
            try:
                llm_candidates = identify_quote_candidates(
                    params.quote,
                    cfg,
                    provider_override=params.provider,
                    model_override=params.model,
                    show_hint=params.show,
                    movie=params.movie,
                    max_candidates=params.candidates,
                )
            except Exception as e:
                raise FindError(f"Identification failed: {e}") from e

        llm_candidate_infos = [_episode_ref_to_info(c) for c in llm_candidates]

        if params.llm_candidate_index is not None:
            idx = params.llm_candidate_index
            if idx < 0 or idx >= len(llm_candidates):
                raise FindError(f"Invalid LLM candidate index: {idx}")
            ref = llm_candidates[idx]
        else:
            ref = llm_candidates[0]

        if params.show and not params.movie and (ref.season is None or ref.episode is None):
            raise FindError(
                f"Could not determine season/episode within {params.show}. "
                "Try a more specific quote or pass an episode like S01E12."
            )

        if ref.confidence < 0.6 and not params.auto_confirm and not params.cached_llm_candidates:
            raise FindInputRequired(
                "low_confidence",
                f"Low confidence ({ref.confidence:.0%}) on top guess: {ref.reasoning}",
                ref=ref,
                llm_candidates=llm_candidates,
            )

    progress("library", f"Searching for {ref.display()}")
    with timer.track("Load library index"):
        entries = get_index(cfg)

    if not skip_llm and llm_candidates:
        progress("verify", "Verifying LLM picks against subtitles")
        with timer.track("Episode verification"):
            resolved = resolve_episode(
                llm_candidates,
                params.quote,
                entries,
                show=params.show,
            )
        if resolved is not None:
            ref = resolved.ref
            resolved_media_path = resolved.media_path
            resolved_pick_reason = resolved.reason

    if params.media_path:
        media_path = Path(params.media_path)
        pick_reason = "user-selected file"
        if not media_path.exists():
            raise FindError(f"Selected file not found: {media_path}")
    elif resolved_media_path is not None:
        media_path = resolved_media_path
        pick_reason = resolved_pick_reason or "subtitle resolved"
    else:
        matches = find_media(ref, entries)
        if not matches:
            raise FindError(
                f"No matching file found for {ref.display()}. "
                "Run quotegif index to rebuild the index."
            )

        try:
            progress("select", "Selecting media file")
            with timer.track("Select media file"):
                media_path, pick_reason = select_media_file(
                    ref, params.quote, matches, cfg
                )
        except LookupError as e:
            if len(matches) > 1 and not params.auto_confirm:
                raise FindInputRequired(
                    "file_pick",
                    str(e),
                    ref=ref,
                    llm_candidates=llm_candidates or [ref],
                    file_candidates=matches,
                ) from e
            if len(matches) == 1:
                media_path = matches[0].path
                pick_reason = "library match (only candidate)"
            else:
                raise FindError(str(e)) from e

    time_hint = around_seconds if around_seconds is not None else ref.approx_timestamp
    if time_hint is not None:
        source = "around" if around_seconds is not None else "LLM approx_timestamp"
        progress(
            "locate",
            f"Whisper clip hint ({source}): {format_timestamp(time_hint)}",
        )
    else:
        progress("locate", "Locating quote in file")

    with timer.track("Locate quote in file"):
        try:
            locate = locate_quote(
                ref,
                params.quote,
                cfg,
                media_path,
                around_seconds=around_seconds,
            )
        except LookupError as e:
            raise FindError(str(e)) from e
        except ImportError as e:
            raise FindError(f"Whisper not available: {e}") from e
        except Exception as e:
            raise FindError(f"Location failed: {e}") from e

    progress("render", params.output_format)
    with timer.track("Render output", detail=params.output_format):
        try:
            out_path = render_output(
                locate, cfg, ref.display(), params.output_format
            )
        except RuntimeError as e:
            raise FindError(f"Render failed: {e}") from e

    timings = [
        TimingStepInfo(
            name=step.name,
            detail=step.detail,
            seconds=step.seconds,
        )
        for step in timer.steps
    ]

    return FindResult(
        ref=ref,
        media_path=str(media_path),
        pick_reason=pick_reason,
        locate=locate,
        output_path=str(out_path),
        output_format=params.output_format,
        llm_candidates=llm_candidate_infos,
        matched=_matched_from_locate(locate),
        timings=timings,
        total_seconds=timer.total_seconds,
    )
