from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from quotegif.models import SubCue

_SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}


def _find_sidecar(media_path: Path) -> Path | None:
    """Look for a subtitle file adjacent to the media file."""
    stem = media_path.stem
    parent = media_path.parent
    for ext in (".srt", ".ass", ".ssa", ".vtt"):
        candidate = parent / f"{stem}{ext}"
        if candidate.exists():
            return candidate
        # language-tagged variants, e.g. Show.S01E01.en.srt
        for lang_file in parent.glob(f"{stem}.*.{ext.lstrip('.')}"):
            return lang_file
    return None


def _ffprobe_subtitle_streams(media_path: Path) -> list[int]:
    """Return stream indices of subtitle tracks in the file."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        indices = [int(x) for x in result.stdout.strip().splitlines() if x.strip().isdigit()]
        return indices
    except Exception:
        return []


def _extract_embedded_srt(media_path: Path, stream_index: int) -> Path | None:
    """Extract a subtitle stream to a temporary .srt file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".srt", delete=False)
    tmp.close()
    out_path = Path(tmp.name)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(media_path),
                "-map", f"0:{stream_index}",
                str(out_path),
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0 and out_path.stat().st_size > 0:
            return out_path
    except Exception:
        pass
    out_path.unlink(missing_ok=True)
    return None


def _parse_srt(path: Path) -> list[SubCue]:
    try:
        import srt
    except ImportError as e:
        raise ImportError("srt package not installed. Run: pip install quotegif") from e

    text = path.read_text(encoding="utf-8", errors="replace")
    cues: list[SubCue] = []
    for sub in srt.parse(text):
        clean = re.sub(r"<[^>]+>", " ", sub.content)  # strip HTML tags
        clean = re.sub(r"\{[^}]+\}", " ", clean)       # strip ASS tags
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            continue
        cues.append(SubCue(
            start=sub.start.total_seconds(),
            end=sub.end.total_seconds(),
            text=clean,
            index=sub.index,
        ))
    return cues


def _parse_ass(path: Path) -> list[SubCue]:
    """Very simple ASS/SSA parser extracting dialogue lines."""
    cues: list[SubCue] = []
    idx = 0
    in_events = False
    format_map: dict[str, int] = {}

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.lower() == "[events]":
            in_events = True
            continue
        if line.startswith("[") and line.endswith("]"):
            in_events = False
        if not in_events:
            continue
        if line.lower().startswith("format:"):
            parts = [p.strip().lower() for p in line[7:].split(",")]
            format_map = {name: i for i, name in enumerate(parts)}
            continue
        if not line.lower().startswith("dialogue:"):
            continue
        cols = line[9:].split(",", len(format_map) - 1)
        if not format_map:
            continue

        def _col(name: str) -> str:
            i = format_map.get(name)
            return cols[i].strip() if i is not None and i < len(cols) else ""

        def _time_to_secs(t: str) -> float:
            try:
                parts = t.split(":")
                h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
                return h * 3600 + m * 60 + s
            except Exception:
                return 0.0

        start = _time_to_secs(_col("start"))
        end = _time_to_secs(_col("end"))
        text = _col("text")
        text = re.sub(r"\{[^}]*\}", "", text).replace("\\N", " ").strip()
        if text:
            cues.append(SubCue(start=start, end=end, text=text, index=idx))
            idx += 1

    return cues


def get_cue_source(media_path: Path) -> str:
    """Describe where subtitles would be loaded from (for verbose logging)."""
    sidecar = _find_sidecar(media_path)
    if sidecar is not None:
        return f"sidecar {sidecar.name}"

    streams = _ffprobe_subtitle_streams(media_path)
    if streams:
        return f"embedded stream index {streams[0]}"

    return "none"


def get_cues(media_path: Path) -> list[SubCue]:
    """
    Load subtitle cues for a media file.
    Tries: sidecar file -> embedded subtitle stream.
    Returns empty list if no subtitles found.
    """
    sidecar = _find_sidecar(media_path)
    sub_path: Path | None = sidecar

    if sub_path is None:
        streams = _ffprobe_subtitle_streams(media_path)
        for stream_idx in streams:
            extracted = _extract_embedded_srt(media_path, stream_idx)
            if extracted:
                sub_path = extracted
                break

    if sub_path is None:
        return []

    ext = sub_path.suffix.lower()
    if ext in (".ass", ".ssa"):
        return _parse_ass(sub_path)
    return _parse_srt(sub_path)
