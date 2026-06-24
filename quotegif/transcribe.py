from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from quotegif.models import SubCue


def get_media_duration(media_path: Path) -> float | None:
    """Return media duration in seconds, or None if unknown."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except Exception:
        return None


def extract_audio_clip(media_path: Path, start: float, end: float) -> Path:
    """Extract a mono 16 kHz WAV clip for Whisper. Returns a temp file path."""
    duration = max(0.1, end - start)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    out_path = Path(tmp.name)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start:.3f}",
            "-i", str(media_path),
            "-t", f"{duration:.3f}",
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        timeout=max(120, int(duration) + 60),
    )
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed to extract audio clip: {result.stderr.strip()}")
    return out_path


def _run_whisper(
    audio_path: Path,
    model_name: str,
    device: str,
) -> list[SubCue]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ImportError(
            "faster-whisper not installed. Run: pip install 'quotegif[whisper]'"
        ) from e

    resolved_device = device
    if device == "auto":
        try:
            import torch  # type: ignore[import]
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            try:
                import ctranslate2
                resolved_device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                resolved_device = "cpu"

    compute_type = "float16" if resolved_device == "cuda" else "int8"
    model = WhisperModel(model_name, device=resolved_device, compute_type=compute_type)
    segments, _ = model.transcribe(str(audio_path), beam_size=5, word_timestamps=True)

    cues: list[SubCue] = []
    for i, segment in enumerate(segments):
        text = segment.text.strip()
        if not text:
            continue
        cues.append(SubCue(
            start=segment.start,
            end=segment.end,
            text=text,
            index=i,
        ))
    return cues


def transcribe(
    media_path: Path,
    model_name: str = "base",
    device: str = "auto",
    *,
    window_start: float | None = None,
    window_end: float | None = None,
) -> list[SubCue]:
    """
    Transcribe audio from media_path using faster-whisper.

    When window_start/window_end are set, only that portion of the file is
    transcribed. Cue timestamps are absolute (relative to the full media file).
    """
    time_offset = 0.0
    audio_path = media_path
    temp_audio: Path | None = None

    if window_start is not None and window_end is not None:
        time_offset = window_start
        temp_audio = extract_audio_clip(media_path, window_start, window_end)
        audio_path = temp_audio

    try:
        cues = _run_whisper(audio_path, model_name, device)
    finally:
        if temp_audio is not None:
            temp_audio.unlink(missing_ok=True)

    if time_offset:
        for cue in cues:
            cue.start += time_offset
            cue.end += time_offset

    return cues


def compute_whisper_window(
    center: float,
    clip_window: float,
    media_duration: float | None,
) -> tuple[float, float]:
    """Return (start, end) seconds for a centered clip-window transcription."""
    half = clip_window / 2.0
    start = max(0.0, center - half)
    end = center + half
    if media_duration is not None:
        end = min(end, media_duration)
    if end <= start:
        end = start + 30.0
        if media_duration is not None:
            end = min(end, media_duration)
    return start, end
