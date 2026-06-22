from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from quotegif.models import ClipSpec
from quotegif.utils import sanitize_filename, seconds_to_timestamp


def make_gif(
    spec: ClipSpec,
    output_dir: Path,
    fps: int = 12,
    width: int = 480,
    episode_label: str = "clip",
) -> Path:
    """
    Render an animated GIF from the clip spec using a two-pass ffmpeg approach
    (palettegen + paletteuse) for high colour quality.

    Returns the path to the output .gif file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    ts_label = seconds_to_timestamp(spec.cue.start).replace(":", "-").replace(".", "-")
    label = sanitize_filename(episode_label)
    out_path = output_dir / f"{label}__{ts_label}.gif"

    start = spec.clip_start
    duration = spec.duration

    # Build the complex filtergraph:
    # [0:v] fps=N, scale=W:-1:flags=lanczos -> split -> palettegen / paletteuse
    vf_palette = (
        f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
        f"[s0]palettegen=max_colors=128:stats_mode=diff[p];"
        f"[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        palette_path = Path(tmpdir) / "palette.png"

        # Pass 1: generate palette
        pass1 = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", str(spec.media_path),
            "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen=max_colors=128:stats_mode=diff",
            "-frames:v", "1",
            str(palette_path),
        ]
        result1 = subprocess.run(pass1, capture_output=True)
        if result1.returncode != 0:
            raise RuntimeError(
                f"ffmpeg palette pass failed:\n{result1.stderr.decode(errors='replace')}"
            )

        # Pass 2: render GIF using the palette
        pass2 = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", str(spec.media_path),
            "-i", str(palette_path),
            "-filter_complex",
            (
                f"fps={fps},scale={width}:-1:flags=lanczos[x];"
                f"[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
            ),
            str(out_path),
        ]
        result2 = subprocess.run(pass2, capture_output=True)
        if result2.returncode != 0:
            raise RuntimeError(
                f"ffmpeg GIF render failed:\n{result2.stderr.decode(errors='replace')}"
            )

    return out_path
