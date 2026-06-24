from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from quotegif.models import ClipSpec, SubCue
from quotegif.subtitle_render import subtitle_filter_arg, write_srt
from quotegif.utils import sanitize_filename, seconds_to_timestamp


def make_gif(
    spec: ClipSpec,
    output_dir: Path,
    fps: int = 12,
    width: int = 480,
    episode_label: str = "clip",
    subtitle_cues: list[SubCue] | None = None,
) -> Path:
    """
    Render an animated GIF from the clip spec using a two-pass ffmpeg approach
    (palettegen + paletteuse) for high colour quality.

    When subtitle_cues is provided, dialogue is burned into every frame.
    GIF mode should always pass subtitles so the quote is readable without audio.
    """
    if not subtitle_cues:
        raise ValueError(
            "GIF rendering requires subtitle_cues. Use clip format for video with audio."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    ts_label = seconds_to_timestamp(spec.cue.start).replace(":", "-").replace(".", "-")
    label = sanitize_filename(episode_label)
    out_path = output_dir / f"{label}__{ts_label}.gif"

    start = spec.clip_start
    duration = spec.duration

    font_size = max(14, min(28, width // 24))
    scale_vf = f"fps={fps},scale={width}:-1:flags=lanczos"

    with tempfile.TemporaryDirectory() as tmpdir:
        palette_path = Path(tmpdir) / "palette.png"
        srt_path = Path(tmpdir) / "clip.srt"
        write_srt(subtitle_cues, srt_path)

        subs_vf = subtitle_filter_arg(srt_path, font_size=font_size)
        pre_palette_vf = f"{scale_vf},{subs_vf}"

        pass1 = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", str(spec.media_path),
            "-vf", f"{pre_palette_vf},palettegen=max_colors=128:stats_mode=diff",
            "-frames:v", "1",
            str(palette_path),
        ]
        result1 = subprocess.run(pass1, capture_output=True)
        if result1.returncode != 0:
            raise RuntimeError(
                f"ffmpeg palette pass failed:\n{result1.stderr.decode(errors='replace')}"
            )

        pass2 = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", str(spec.media_path),
            "-i", str(palette_path),
            "-filter_complex",
            (
                f"[0:v]{pre_palette_vf}[x];"
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
