from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from quotegif.transcribe import get_media_duration


def trim_media(source: Path, dest: Path, trim_start: float, trim_end: float) -> None:
    """Extract [trim_start, trim_end) seconds from source into dest."""
    if trim_start < 0:
        raise ValueError("trim_start must be >= 0")
    if trim_end <= trim_start:
        raise ValueError("trim_end must be greater than trim_start")

    duration = trim_end - trim_start
    dest.parent.mkdir(parents=True, exist_ok=True)

    if source.suffix.lower() == ".gif":
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{trim_start:.3f}",
            "-i", str(source),
            "-t", f"{duration:.3f}",
            str(dest),
        ]
    else:
        copy_cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{trim_start:.3f}",
            "-to", f"{trim_end:.3f}",
            "-i", str(source),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(dest),
        ]
        result = subprocess.run(copy_cmd, capture_output=True, text=True)
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return
        dest.unlink(missing_ok=True)
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{trim_start:.3f}",
            "-to", f"{trim_end:.3f}",
            "-i", str(source),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            str(dest),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg trim failed: {result.stderr.strip()}")
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced an empty trimmed file")


def build_trim_output_path(
    source: Path,
    trim_start: float,
    trim_end: float,
) -> Path:
    stem = source.stem
    suffix = source.suffix or ".mp4"
    tag = f"trim_{trim_start:.1f}-{trim_end:.1f}s".replace(".", "p")
    return source.parent / f"{stem}__{tag}__{uuid.uuid4().hex[:8]}{suffix}"


def probe_duration(path: Path) -> float:
    duration = get_media_duration(path)
    if duration is None or duration <= 0:
        raise RuntimeError(f"Could not read duration of {path.name}")
    return duration
