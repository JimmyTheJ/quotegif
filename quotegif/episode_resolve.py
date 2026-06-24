from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quotegif import verbose as v
from quotegif.library import find_media
from quotegif.matcher import match_quote
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


def _verified_quote_score(
    path: Path,
    queries: list[str],
    *,
    subtitle_threshold: float,
) -> tuple[float, float]:
    """
    Return (fuzzy_score, coverage) only when a cue passes the full match gates.
    Returns (0, 0) when no valid match or no searchable subtitles.
    """
    from quotegif.matcher import _rank_text
    from quotegif.utils import normalize_text

    cues = get_cues(path)
    if not cues:
        return 0.0, 0.0

    best_fuzzy = 0.0
    best_coverage = 0.0
    for query in queries:
        cue = match_quote(query, cues, threshold=subtitle_threshold)
        if cue is None:
            continue
        rank = _rank_text(normalize_text(query), cue.text, merge_span=1)
        if rank.fuzzy > best_fuzzy or (
            rank.fuzzy == best_fuzzy and rank.coverage > best_coverage
        ):
            best_fuzzy = rank.fuzzy
            best_coverage = rank.coverage

    return best_fuzzy, best_coverage


def _llm_episode_keys(candidates: list[EpisodeRef]) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for cand in candidates:
        if cand.season is not None and cand.episode is not None:
            keys.add((cand.season, cand.episode))
    return keys


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
        score, coverage = _verified_quote_score(
            path, queries, subtitle_threshold=subtitle_threshold
        )
        sub_src = get_cue_source(path)
        if v.is_verbose():
            if not cues:
                status = "NO SUBS"
            elif score >= subtitle_threshold:
                status = "PASS"
            else:
                status = "fail"
            v.log(
                f"  [{status}] {cand.display()} score={score:.0f} cov={coverage:.0%} "
                f"cues={len(cues)} subs={sub_src} → {path.name}"
            )
        if score < subtitle_threshold:
            continue
        label = cand.display()
        resolved = ResolvedEpisode(
            ref=cand,
            media_path=path,
            reason=f"subtitle verified LLM pick ({score:.0f}%, cov {coverage:.0%}) — {label}",
            match_score=score,
        )
        if best is None or score > best.match_score:
            best = resolved

    return best


def _top_llm_has_no_subtitles(
    candidates: list[EpisodeRef],
    entries: list[MediaEntry],
) -> bool:
    """True when the top LLM pick exists on disk but has no searchable subtitles."""
    if not candidates:
        return False
    top = candidates[0]
    if top.season is None or top.episode is None:
        return False
    matches = find_media(top, entries)
    if not matches:
        return False
    return len(get_cues(matches[0].path)) == 0


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

    llm_keys = _llm_episode_keys(candidates)

    if v.is_verbose():
        v.section(f"Show subtitle scan ({show})")
        v.log(f"Scanning {len(show_entries)} episode file(s), threshold {subtitle_threshold:.0f}")

    best: ResolvedEpisode | None = None
    best_coverage = 0.0
    top_scores: list[tuple[float, float, str]] = []

    for entry in show_entries:
        score, coverage = _verified_quote_score(
            entry.path, queries, subtitle_threshold=subtitle_threshold
        )
        label = (
            f"S{entry.season:02d}E{entry.episode:02d}"
            if entry.season and entry.episode
            else entry.path.name
        )
        if score > 0:
            top_scores.append((score, coverage, label))
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
        is_llm_pick = (
            entry.season is not None
            and entry.episode is not None
            and (entry.season, entry.episode) in llm_keys
        )
        resolved = ResolvedEpisode(
            ref=ref,
            media_path=entry.path,
            reason=f"subtitle scan ({score:.0f}%, cov {coverage:.0%}) — {ep_label}",
            match_score=score,
        )

        def _better_candidate() -> bool:
            if best is None:
                return True
            if score > best.match_score:
                return True
            if score == best.match_score:
                if coverage > best_coverage:
                    return True
                if coverage == best_coverage and is_llm_pick:
                    best_is_llm = (
                        best.ref.season is not None
                        and best.ref.episode is not None
                        and (best.ref.season, best.ref.episode) in llm_keys
                    )
                    return is_llm_pick and not best_is_llm
            return False

        if _better_candidate():
            best = resolved
            best_coverage = coverage

    if v.is_verbose() and top_scores:
        top_scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
        v.log("Top verified subtitle scores across show:")
        for score, coverage, label in top_scores[:10]:
            mark = " ← selected" if best and label in best.reason else ""
            v.log(f"  {score:.0f}  cov={coverage:.0%}  {label}{mark}")

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
    2. If show is known and nothing verified, scan all episodes' subtitle files —
       unless the top LLM file has no searchable subs (keep LLM pick for Whisper).
    """
    verified = verify_llm_candidates(
        candidates, quote, entries, subtitle_threshold=subtitle_threshold
    )
    if verified is not None:
        return verified

    if show and _top_llm_has_no_subtitles(candidates, entries):
        if v.is_verbose():
            v.log(
                "Top LLM episode has no searchable subtitles — skipping show-wide scan; "
                "using LLM episode (Whisper may be needed)."
            )
        return None

    if show:
        return scan_show_subtitles(
            show, quote, candidates, entries, subtitle_threshold=subtitle_threshold
        )

    return None
