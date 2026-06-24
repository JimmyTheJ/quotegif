from __future__ import annotations

import re
from pathlib import Path

from quotegif.config import AppConfig
from quotegif.models import EpisodeRef, MediaEntry

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
    from quotegif.matcher import match_quote
    from quotegif.subtitles import get_cues

    if not candidates:
        raise LookupError(f"No matching file found for {ref.display()}")

    # One candidate and we know the exact episode — trust the library index.
    if len(candidates) == 1 and ref.season is not None and ref.episode is not None:
        return candidates[0].path, "single library match"

    search_query = ref.exact_quote or quote
    best_entry: MediaEntry | None = None
    best_score = 0.0

    for entry in candidates:
        cues = get_cues(entry.path)
        if not cues:
            continue
        cue = match_quote(search_query, cues, threshold=subtitle_threshold)
        if cue is None:
            continue
        # Re-score for ranking among files (match_quote already filtered by threshold).
        from quotegif.matcher import score_cues

        score = score_cues(search_query, cues, top_n=1)[0][0]
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry is not None:
        label = best_entry.path.name
        if best_entry.season and best_entry.episode:
            label = f"S{best_entry.season:02d}E{best_entry.episode:02d} — {label}"
        return best_entry.path, f"subtitle match ({best_score:.0f}%) in {label}"

    # No subtitle hit — fall back to top library match if episode was specified.
    if ref.season is not None and ref.episode is not None:
        return candidates[0].path, "library match (no subtitle hit for quote)"

    raise LookupError(
        f"Found {len(candidates)} possible files for {ref.title}, but the quote "
        "was not found in any of their subtitles. "
        "Try a more specific quote, pass --episode \"Show S01E02\", or ensure subtitles exist."
    )
