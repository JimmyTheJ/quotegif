from __future__ import annotations

from rapidfuzz import fuzz, process

from quotegif.models import SubCue
from quotegif.utils import normalize_text

_DEFAULT_THRESHOLD = 45.0  # minimum score (0–100) to accept a match


def best_quote_score(
    query: str,
    cues: list[SubCue],
    window: int = 3,
) -> tuple[float, SubCue | None]:
    """Return the best fuzzy score and matching cue (cue is None if below threshold)."""
    if not cues:
        return 0.0, None

    query_norm = normalize_text(query)

    def _score(text: str) -> float:
        return max(
            fuzz.partial_ratio(query_norm, normalize_text(text)),
            fuzz.token_sort_ratio(query_norm, normalize_text(text)),
        )

    best_score = 0.0
    best_cue: SubCue | None = None

    for cue in cues:
        s = _score(cue.text)
        if s > best_score:
            best_score = s
            best_cue = cue

    for i in range(len(cues)):
        for w in range(2, min(window + 1, len(cues) - i + 1)):
            merged_text = " ".join(c.text for c in cues[i: i + w])
            s = _score(merged_text)
            if s > best_score:
                best_score = s
                best_cue = SubCue(
                    start=cues[i].start,
                    end=cues[i + w - 1].end,
                    text=merged_text,
                    index=cues[i].index,
                )

    if best_score >= _DEFAULT_THRESHOLD:
        return best_score, best_cue
    return best_score, None


def match_quote(
    query: str,
    cues: list[SubCue],
    threshold: float = _DEFAULT_THRESHOLD,
    window: int = 3,
) -> SubCue | None:
    """
    Find the subtitle cue that best matches the query string.

    Uses partial_ratio on normalized text, and also checks merged windows
    of adjacent cues to handle quotes that span multiple subtitle lines.

    Returns the best matching SubCue, or None if no cue exceeds the threshold.
    """
    score, cue = best_quote_score(query, cues, window=window)
    if score >= threshold:
        return cue
    return None


def score_cues(
    query: str,
    cues: list[SubCue],
    top_n: int = 5,
) -> list[tuple[float, SubCue]]:
    """Return the top N (score, cue) pairs for diagnostic/debugging."""
    query_norm = normalize_text(query)
    results = []
    for cue in cues:
        s = max(
            fuzz.partial_ratio(query_norm, normalize_text(cue.text)),
            fuzz.token_sort_ratio(query_norm, normalize_text(cue.text)),
        )
        results.append((s, cue))
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_n]
