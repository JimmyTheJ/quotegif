from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quotegif import verbose as v
from quotegif.library import find_media
from quotegif.matcher import best_quote_score
from quotegif.models import EpisodeRef, MediaEntry
from quotegif.subtitles import get_cue_source, get_cues

_DEFAULT_THRESHOLD = 45.0


@dataclass
class ResolvedEpisode:
    ref: EpisodeRef
    media_path: Path
    reason: str
    match_score: float


def _search_queries(quote: str, candidates: list[EpisodeRef]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for text in [quote, *(c.exact_quote for c in candidates if c.exact_quote)]:
        norm = text.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            queries.append(text)
    return queries


def _score_file(
    path: Path,
    queries: list[str],
) -> float:
    cues = get_cues(path)
    if not cues:
        return 0.0
    best_score = 0.0
    for query in queries:
        score, _ = best_quote_score(query, cues)
        best_score = max(best_score, score)
    return best_score


def verify_llm_candidates(
    candidates: list[EpisodeRef],
    quote: str,
    entries: list[MediaEntry],
    *,
    subtitle_threshold: float = _DEFAULT_THRESHOLD,
) -> ResolvedEpisode | None:
    """Check LLM episode guesses against on-disk subtitles; return best verified hit."""
    queries = _search_queries(quote, candidates)
    best: ResolvedEpisode | None = None

    if v.is_verbose():
        v.section("Episode verification (LLM candidates)")
        v.log(f"Search queries: {queries!r}")
        v.log(f"Subtitle match threshold: {subtitle_threshold:.0f}")

    for cand in candidates:
        if cand.season is None or cand.episode is None:
            if v.is_verbose():
                v.log(f"  skip {cand.display()} — no season/episode")
            continue
        matches = find_media(cand, entries)
        if not matches:
            if v.is_verbose():
                v.log(f"  skip {cand.display()} — no library file")
            continue
        path = matches[0].path
        cues = get_cues(path)
        score = _score_file(path, queries)
        sub_src = get_cue_source(path)
        if v.is_verbose():
            status = "PASS" if score >= subtitle_threshold else "fail"
            v.log(
                f"  [{status}] {cand.display()} score={score:.0f} "
                f"cues={len(cues)} subs={sub_src} → {path.name}"
            )
        if score < subtitle_threshold:
            continue
        label = cand.display()
        resolved = ResolvedEpisode(
            ref=cand,
            media_path=path,
            reason=f"subtitle verified LLM pick ({score:.0f}%) — {label}",
            match_score=score,
        )
        if best is None or score > best.match_score:
            best = resolved

    return best


def scan_show_subtitles(
    show: str,
    quote: str,
    candidates: list[EpisodeRef],
    entries: list[MediaEntry],
    *,
    subtitle_threshold: float = _DEFAULT_THRESHOLD,
) -> ResolvedEpisode | None:
    """Scan subtitle files across every episode of a show (no Whisper)."""
    queries = _search_queries(quote, candidates)
    show_ref = EpisodeRef(title=show, media_type="tv", exact_quote=quote)
    show_entries = find_media(show_ref, entries)
    if not show_entries:
        return None

    if v.is_verbose():
        v.section(f"Show subtitle scan ({show})")
        v.log(f"Scanning {len(show_entries)} episode file(s), threshold {subtitle_threshold:.0f}")

    best: ResolvedEpisode | None = None
    top_scores: list[tuple[float, str]] = []

    for entry in show_entries:
        score = _score_file(entry.path, queries)
        label = (
            f"S{entry.season:02d}E{entry.episode:02d}"
            if entry.season and entry.episode
            else entry.path.name
        )
        if score > 0:
            top_scores.append((score, label))
        if score < subtitle_threshold:
            continue
        ref = EpisodeRef(
            title=show,
            media_type="tv",
            season=entry.season,
            episode=entry.episode,
            exact_quote=quote,
            confidence=1.0,
            reasoning="Found via subtitle scan across show",
        )
        ep_label = ref.display()
        resolved = ResolvedEpisode(
            ref=ref,
            media_path=entry.path,
            reason=f"subtitle scan ({score:.0f}%) — {ep_label}",
            match_score=score,
        )
        if best is None or score > best.match_score:
            best = resolved

    if v.is_verbose() and top_scores:
        top_scores.sort(reverse=True)
        v.log("Top subtitle scores across show:")
        for score, label in top_scores[:10]:
            mark = " ← selected" if best and label in best.reason else ""
            v.log(f"  {score:.0f}  {label}{mark}")

    return best


def resolve_episode(
    candidates: list[EpisodeRef],
    quote: str,
    entries: list[MediaEntry],
    *,
    show: str | None = None,
    subtitle_threshold: float = _DEFAULT_THRESHOLD,
) -> ResolvedEpisode | None:
    """
    Prefer subtitle evidence over LLM episode numbers.

    1. Verify each LLM candidate against that episode's subtitles.
    2. If show is known and nothing verified, scan all episodes' subtitle files.
    """
    verified = verify_llm_candidates(
        candidates, quote, entries, subtitle_threshold=subtitle_threshold
    )
    if verified is not None:
        return verified

    if show:
        return scan_show_subtitles(
            show, quote, candidates, entries, subtitle_threshold=subtitle_threshold
        )

    return None
