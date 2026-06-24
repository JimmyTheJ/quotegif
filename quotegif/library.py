from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from quotegif.media_select import is_extra_content
from quotegif.models import EpisodeRef, MediaEntry
from quotegif.utils import VIDEO_EXTENSIONS, normalize_text

if TYPE_CHECKING:
    from quotegif.config import AppConfig

_CACHE_PATH = Path.home() / ".cache" / "quotegif" / "index.json"

_TITLE_STOPWORDS = frozenset({
    "the", "a", "an", "and", "of", "in", "to", "for", "on", "at",
    "star", "trek",
})

# Equivalent names for the same show (any token overlap links the whole group).
_SHOW_EQUIVALENTS: list[frozenset[str]] = [
    frozenset({"deep", "space", "nine", "ds9"}),
    frozenset({"next", "generation", "tng"}),
    frozenset({"original", "series", "tos"}),
    frozenset({"voyager", "voy"}),
    frozenset({"enterprise", "ent"}),  # ST:Enterprise — distinct from DS9/TNG
]


def _significant_title_tokens(title: str) -> set[str]:
    tokens = set(normalize_text(title).split())
    sig = {t for t in tokens if len(t) > 2 and t not in _TITLE_STOPWORDS}
    return sig or tokens


def _expand_ref_tokens(ref_sig: set[str]) -> set[str]:
    expanded = set(ref_sig)
    for group in _SHOW_EQUIVALENTS:
        if ref_sig & group:
            expanded |= group
    return expanded


def _entry_tokens(entry: MediaEntry) -> set[str]:
    text = normalize_text(entry.title) + " " + _path_context_text(entry.path)
    return set(text.split())


def _show_identity_matches(ref_sig: set[str], entry: MediaEntry) -> bool:
    """True when ref and entry refer to the same series (handles DS9 vs Deep Space Nine)."""
    if not ref_sig:
        return True
    entry_tok = _entry_tokens(entry)
    for group in _SHOW_EQUIVALENTS:
        if (ref_sig & group) and (entry_tok & group):
            return True
    expanded = _expand_ref_tokens(ref_sig)
    return len(expanded & entry_tok) / len(ref_sig) >= 0.34


def _path_context_text(path: Path) -> str:
    """Use parent folders + filename — guessit often misses the real show name."""
    parts = list(path.parts[-5:])
    return normalize_text(" ".join(parts))


def _title_overlap(ref_sig: set[str], entry: MediaEntry) -> float:
    if not ref_sig:
        return 1.0
    if _show_identity_matches(ref_sig, entry):
        return 1.0
    expanded = _expand_ref_tokens(ref_sig)
    entry_tok = _entry_tokens(entry)
    return len(expanded & entry_tok) / len(ref_sig)


def _first_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _guess_entry(path: Path) -> MediaEntry | None:
    try:
        import guessit
    except ImportError as e:
        raise ImportError("guessit not installed. Run: pip install quotegif") from e

    try:
        info = guessit.guessit(path.name)
    except Exception:
        return None

    title = info.get("title")
    if not title:
        return None

    media_type: str = "movie"
    if info.get("type") == "episode" or info.get("season") is not None:
        media_type = "tv"

    return MediaEntry(
        path=path,
        title=str(title),
        media_type=media_type,  # type: ignore[arg-type]
        season=_first_int(info.get("season")),
        episode=_first_int(info.get("episode")),
        year=_first_int(info.get("year")),
        raw_guess=dict(info),
    )


def build_index(folders: list[Path], verbose: bool = False) -> list[MediaEntry]:
    entries: list[MediaEntry] = []
    skipped_extras = 0
    for folder in folders:
        if not folder.exists():
            if verbose:
                print(f"[warning] folder not found, skipping: {folder}")
            continue
        for path in folder.rglob("*"):
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if is_extra_content(path):
                skipped_extras += 1
                continue
            entry = _guess_entry(path)
            if entry:
                entries.append(entry)
    if verbose and skipped_extras:
        print(f"[info] skipped {skipped_extras} extras/deleted-scene files")
    return entries


def _entry_to_dict(e: MediaEntry) -> dict:
    return {
        "path": str(e.path),
        "title": e.title,
        "media_type": e.media_type,
        "season": e.season,
        "episode": e.episode,
        "year": e.year,
    }


def _dict_to_entry(d: dict) -> MediaEntry:
    return MediaEntry(
        path=Path(d["path"]),
        title=d["title"],
        media_type=d["media_type"],
        season=d.get("season"),
        episode=d.get("episode"),
        year=d.get("year"),
    )


def save_index(entries: list[MediaEntry], path: Path = _CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "built_at": time.time(),
        "entries": [_entry_to_dict(e) for e in entries],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_cached_index(path: Path = _CACHE_PATH) -> list[MediaEntry] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [_dict_to_entry(d) for d in data.get("entries", [])]
    except Exception:
        return None


def get_index(config: "AppConfig", force_rebuild: bool = False) -> list[MediaEntry]:
    if not force_rebuild:
        cached = load_cached_index()
        if cached is not None:
            return cached
    entries = build_index(config.media_folders)
    save_index(entries)
    return entries


def find_media(ref: EpisodeRef, entries: list[MediaEntry]) -> list[MediaEntry]:
    """Match an EpisodeRef against the library index. Returns ranked list (best first)."""
    return [entry for _, entry in rank_media_matches(ref, entries)]


def rank_media_matches(
    ref: EpisodeRef,
    entries: list[MediaEntry],
) -> list[tuple[float, MediaEntry]]:
    """
    Match an EpisodeRef against the library index with scores (best first).
    """
    ref_title_norm = normalize_text(ref.title)
    ref_sig = _significant_title_tokens(ref.title)
    min_title = 75 if ref.season is None and ref.episode is None else 60
    candidates: list[tuple[float, MediaEntry]] = []

    for entry in entries:
        if is_extra_content(entry.path):
            continue

        entry_title_norm = normalize_text(entry.title)
        path_text = _path_context_text(entry.path)
        title_score = max(
            fuzz.token_sort_ratio(ref_title_norm, entry_title_norm),
            fuzz.partial_ratio(ref_title_norm, path_text),
        )
        identity_match = _show_identity_matches(ref_sig, entry)

        if not identity_match and title_score < min_title:
            continue
        if identity_match:
            title_score = max(title_score, 80)

        overlap = _title_overlap(ref_sig, entry)
        if ref_sig and overlap < 0.34:
            continue

        if ref.media_type == "tv":
            if entry.media_type != "tv":
                continue
            if ref.season is not None and entry.season != ref.season:
                continue
            if ref.episode is not None and entry.episode != ref.episode:
                continue

        score = float(title_score) + overlap * 30.0
        if ref_title_norm == entry_title_norm:
            score += 20
        if ref_title_norm in path_text:
            score += 25
        if ref.media_type == "movie" and ref.season is None:
            score += 5

        candidates.append((score, entry))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates
