from __future__ import annotations

import subprocess
from pathlib import Path

from quotegif.models import ClipSpec
from quotegif.utils import sanitize_filename, seconds_to_timestamp


def make_clip(
    spec: ClipSpec,
    output_dir: Path,
    episode_label: str = "clip",
) -> Path:
    """
    Extract a video clip with audio, preserving the source container/codec when possible.

    Uses stream copy (-c copy) first; falls back to re-encoding if that fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    ext = spec.media_path.suffix or ".mkv"
    ts_label = seconds_to_timestamp(spec.cue.start).replace(":", "-").replace(".", "-")
    label = sanitize_filename(episode_label)
    out_path = output_dir / f"{label}__{ts_label}{ext}"

    start = spec.clip_start
    duration = spec.duration

    copy_cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(spec.media_path),
        "-t", str(duration),
        "-map", "0",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(out_path),
    ]
    result = subprocess.run(copy_cmd, capture_output=True)
    if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    # Stream copy can fail at non-keyframe cuts — re-encode as fallback.
    out_path.unlink(missing_ok=True)
    encode_cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(spec.media_path),
        "-t", str(duration),
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        str(out_path),
    ]
    result = subprocess.run(encode_cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg clip extraction failed:\n{result.stderr.decode(errors='replace')}"
        )

    return out_path
