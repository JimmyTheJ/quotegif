from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from quotegif.models import EpisodeRef, MediaEntry
from quotegif.utils import VIDEO_EXTENSIONS, normalize_text

if TYPE_CHECKING:
    from quotegif.config import AppConfig

_CACHE_PATH = Path.home() / ".cache" / "quotegif" / "index.json"


def _first_int(value: object) -> int | None:
    """Coerce a guessit value (int, str, or list thereof) to a single int, or None."""
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
    """Walk all configured folders and build a MediaEntry list."""
    entries: list[MediaEntry] = []
    for folder in folders:
        if not folder.exists():
            if verbose:
                print(f"[warning] folder not found, skipping: {folder}")
            continue
        for path in folder.rglob("*"):
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            entry = _guess_entry(path)
            if entry:
                entries.append(entry)
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
    """Return cached index or rebuild it."""
    if not force_rebuild:
        cached = load_cached_index()
        if cached is not None:
            return cached
    entries = build_index(config.media_folders)
    save_index(entries)
    return entries


def find_media(ref: EpisodeRef, entries: list[MediaEntry]) -> list[MediaEntry]:
    """
    Match an EpisodeRef against the library index.
    Returns a ranked list (best first); empty if nothing found.
    """
    ref_title_norm = normalize_text(ref.title)
    candidates: list[tuple[float, MediaEntry]] = []

    for entry in entries:
        entry_title_norm = normalize_text(entry.title)
        title_score = fuzz.token_sort_ratio(ref_title_norm, entry_title_norm)

        if title_score < 60:
            continue

        if ref.media_type == "tv":
            if entry.media_type != "tv":
                continue
            if ref.season is not None and entry.season != ref.season:
                continue
            if ref.episode is not None and entry.episode != ref.episode:
                continue

        score = title_score
        # Boost exact title matches
        if ref_title_norm == entry_title_norm:
            score += 20
        # Boost year match for movies
        if ref.media_type == "movie" and ref.season is None:
            score += 5

        candidates.append((score, entry))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in candidates]
