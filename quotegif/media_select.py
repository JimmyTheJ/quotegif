from __future__ import annotations

import re
from pathlib import Path

from quotegif import verbose as v
from quotegif.config import AppConfig
from quotegif.models import EpisodeRef, MediaEntry
from quotegif.subtitles import get_cue_source, get_cues

# Path segments that are almost never the episode you want.
_EXTRA_PATH_RE = re.compile(
    r"(?i)(?:^|[/\\])(?:extras?|deleted[\s_-]*scenes?|bonus|featurettes?|"
    r"trailers?|samples?|interviews?|behind[\s_-]*the[\s_-]*scenes?|outtakes?|"
    r"promos?|sneak[\s_-]*peeks?|deleted|bloopers?)(?:[/\\]|$)"
)


def is_extra_content(path: Path) -> bool:
    """True for deleted scenes, extras, trailers, etc."""
    return bool(_EXTRA_PATH_RE.search(path.as_posix()))


def select_media_file(
    ref: EpisodeRef,
    quote: str,
    candidates: list[MediaEntry],
    cfg: AppConfig,
    *,
    subtitle_threshold: float = 45.0,
) -> tuple[Path, str]:
    """
    Pick the best media file from library candidates.

    When season/episode are known and there is a single strong match, use it.
    Otherwise scan subtitle tracks across candidates for the quote and pick the
    best-scoring episode automatically.

    Returns (media_path, reason_message).
    """
    from quotegif.matcher import best_quote_score, match_quote, score_cues

    if not candidates:
        raise LookupError(f"No matching file found for {ref.display()}")

    if v.is_verbose():
        v.section("File selection")
        v.log(f"Target: {ref.display()}")
        v.log(f"{len(candidates)} library candidate(s), threshold {subtitle_threshold:.0f}")

    # Season/episode known — verify against subtitles when possible before trusting.
    if ref.season is not None and ref.episode is not None:
        path = candidates[0].path
        search_query = ref.exact_quote or quote
        cues = get_cues(path)
        if cues:
            cue = match_quote(search_query, cues, threshold=subtitle_threshold)
            score, _ = best_quote_score(search_query, cues)
            if v.is_verbose():
                v.log(
                    f"  verify {ref.display()}: score={score:.0f} "
                    f"cues={len(cues)} subs={get_cue_source(path)}"
                )
            if cue is not None:
                return path, f"library + subtitle verified ({ref.display()}, {score:.0f}%)"
            if v.is_verbose():
                v.log("  quote not found in expected episode — scanning other candidates")
        else:
            if v.is_verbose():
                v.log(f"  no subtitles on {path.name} — using library match without verify")
            return path, f"library match ({ref.display()}), no subtitles to verify"

    search_query = ref.exact_quote or quote
    best_entry: MediaEntry | None = None
    best_score = 0.0

    for entry in candidates:
        cues = get_cues(entry.path)
        if not cues:
            if v.is_verbose():
                label = (
                    f"S{entry.season:02d}E{entry.episode:02d}"
                    if entry.season and entry.episode
                    else entry.path.name
                )
                v.log(f"  skip {label} — no subtitles ({get_cue_source(entry.path)})")
            continue
        cue = match_quote(search_query, cues, threshold=subtitle_threshold)
        score = score_cues(search_query, cues, top_n=1)[0][0]
        label = (
            f"S{entry.season:02d}E{entry.episode:02d}"
            if entry.season and entry.episode
            else entry.path.name
        )
        if v.is_verbose():
            status = "hit" if cue else "miss"
            v.log(f"  [{status}] {label} score={score:.0f} subs={get_cue_source(entry.path)}")
        if cue is None:
            continue
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry is not None:
        label = best_entry.path.name
        if best_entry.season and best_entry.episode:
            label = f"S{best_entry.season:02d}E{best_entry.episode:02d} — {label}"
        return best_entry.path, f"subtitle match ({best_score:.0f}%) in {label}"

    if ref.season is not None and ref.episode is not None:
        if v.is_verbose():
            v.log("  falling back to top library match (no subtitle hit)")
        return candidates[0].path, "library match (no subtitle hit for quote)"

    raise LookupError(
        f"Found {len(candidates)} possible files for {ref.title}, but the quote "
        "was not found in any of their subtitles. "
        "Try a more specific quote, pass --episode \"Show S01E02\", or ensure subtitles exist."
    )
