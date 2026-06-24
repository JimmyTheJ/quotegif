from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from quotegif.models import SubCue
from quotegif.utils import normalize_text

_DEFAULT_THRESHOLD = 45.0  # minimum fuzzy score (0–100) to accept a match
_MIN_WORDS_FOR_COVERAGE = 3
_MIN_TOKEN_COVERAGE = 0.5  # share of query words that must appear in the cue
_MAX_MERGE_GAP_SECS = 2.0  # only merge subtitle lines this close in time


@dataclass(frozen=True)
class _MatchRank:
    fuzzy: float
    coverage: float  # 0.0–1.0 fraction of query tokens found in cue text
    merge_span: int  # 1 = single cue; >1 = merged adjacent lines
    text_len: int

    @property
    def sort_key(self) -> tuple[float, float, int, int]:
        # Higher is better; prefer fewer merged cues, then shorter text at ties.
        return (self.fuzzy, self.coverage, -self.merge_span, -self.text_len)


def _query_tokens(query_norm: str) -> list[str]:
    return [t for t in query_norm.split() if t]


def _rank_text(query_norm: str, text: str, *, merge_span: int = 1) -> _MatchRank:
    text_norm = normalize_text(text)
    fuzzy = max(
        fuzz.partial_ratio(query_norm, text_norm),
        fuzz.token_sort_ratio(query_norm, text_norm),
        fuzz.token_set_ratio(query_norm, text_norm),
    )
    tokens = _query_tokens(query_norm)
    if not tokens:
        return _MatchRank(0.0, 0.0, merge_span, 0)
    text_token_set = set(text_norm.split())
    covered = sum(1 for t in tokens if t in text_token_set)
    return _MatchRank(fuzzy, covered / len(tokens), merge_span, len(text_norm))


def _is_better(candidate: _MatchRank, current: _MatchRank | None) -> bool:
    if current is None:
        return True
    return candidate.sort_key > current.sort_key


def _passes_threshold(
    rank: _MatchRank,
    query_norm: str,
    threshold: float,
) -> bool:
    if rank.fuzzy < threshold:
        return False
    word_count = len(_query_tokens(query_norm))
    if word_count >= _MIN_WORDS_FOR_COVERAGE and rank.coverage < _MIN_TOKEN_COVERAGE:
        return False
    return True


def _can_merge_window(cues: list[SubCue], start: int, span: int) -> bool:
    """True when subtitle lines are consecutive in time (not scenes far apart)."""
    for j in range(start, start + span - 1):
        gap = cues[j + 1].start - cues[j].end
        if gap > _MAX_MERGE_GAP_SECS:
            return False
    return True


def _best_among_cues(
    query: str,
    cues: list[SubCue],
    *,
    window: int = 3,
    threshold: float = _DEFAULT_THRESHOLD,
) -> tuple[float, float, SubCue | None]:
    """Return (fuzzy_score, token_coverage, best_cue) for the top-ranked acceptable match."""
    if not cues:
        return 0.0, 0.0, None

    query_norm = normalize_text(query)
    best_rank: _MatchRank | None = None
    best_cue: SubCue | None = None

    def _consider(text: str, cue: SubCue, merge_span: int) -> None:
        nonlocal best_rank, best_cue
        rank = _rank_text(query_norm, text, merge_span=merge_span)
        if _is_better(rank, best_rank):
            best_rank = rank
            best_cue = cue

    for cue in cues:
        _consider(cue.text, cue, 1)

    for i in range(len(cues)):
        for w in range(2, min(window + 1, len(cues) - i + 1)):
            if not _can_merge_window(cues, i, w):
                break
            merged_text = " ".join(c.text for c in cues[i: i + w])
            merged = SubCue(
                start=cues[i].start,
                end=cues[i + w - 1].end,
                text=merged_text,
                index=cues[i].index,
            )
            _consider(merged_text, merged, w)

    if best_rank is None or not _passes_threshold(best_rank, query_norm, threshold):
        fuzzy = best_rank.fuzzy if best_rank else 0.0
        cov = best_rank.coverage if best_rank else 0.0
        return fuzzy, cov, None

    return best_rank.fuzzy, best_rank.coverage, best_cue


def best_quote_score(
    query: str,
    cues: list[SubCue],
    window: int = 3,
) -> tuple[float, SubCue | None]:
    """Return the best fuzzy score and matching cue (cue is None if below threshold)."""
    score, _, cue = _best_among_cues(query, cues, window=window)
    return score, cue


def match_quote(
    query: str,
    cues: list[SubCue],
    threshold: float = _DEFAULT_THRESHOLD,
    window: int = 3,
) -> SubCue | None:
    """
    Find the subtitle cue that best matches the query string.

    Uses fuzzy matching with token-coverage gates so short substring hits
    (e.g. matching only "don't" from a longer quote) lose to fuller lines.

    Returns the best matching SubCue, or None if no cue exceeds the threshold.
    """
    _, _, cue = _best_among_cues(query, cues, window=window, threshold=threshold)
    return cue


def top_quote_matches(
    query: str,
    cues: list[SubCue],
    top_n: int = 10,
    window: int = 3,
) -> list[tuple[float, float, SubCue]]:
    """Return top-N (fuzzy_score, token_coverage, cue) pairs."""
    if not cues:
        return []

    query_norm = normalize_text(query)
    scored: list[tuple[_MatchRank, SubCue]] = []

    for cue in cues:
        scored.append((_rank_text(query_norm, cue.text, merge_span=1), cue))

    for i in range(len(cues)):
        for w in range(2, min(window + 1, len(cues) - i + 1)):
            if not _can_merge_window(cues, i, w):
                break
            merged_text = " ".join(c.text for c in cues[i: i + w])
            scored.append((
                _rank_text(query_norm, merged_text, merge_span=w),
                SubCue(
                    start=cues[i].start,
                    end=cues[i + w - 1].end,
                    text=merged_text,
                    index=cues[i].index,
                ),
            ))

    scored.sort(key=lambda x: x[0].sort_key, reverse=True)
    return [(r.fuzzy, r.coverage, c) for r, c in scored[:top_n]]


def score_cues(
    query: str,
    cues: list[SubCue],
    top_n: int = 5,
) -> list[tuple[float, SubCue]]:
    """Return the top N (fuzzy_score, cue) pairs for diagnostic/debugging."""
    query_norm = normalize_text(query)
    results: list[tuple[_MatchRank, SubCue]] = []
    for cue in cues:
        results.append((_rank_text(query_norm, cue.text, merge_span=1), cue))
    results.sort(key=lambda x: x[0].sort_key, reverse=True)
    return [(r.fuzzy, c) for r, c in results[:top_n]]
