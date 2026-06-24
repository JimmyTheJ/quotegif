from __future__ import annotations

from pathlib import Path

from quotegif.models import SubCue


def transcribe(
    media_path: Path,
    model_name: str = "base",
    device: str = "auto",
) -> list[SubCue]:
    """
    Transcribe audio from media_path using faster-whisper.
    Returns a list of SubCue objects with word-level timestamps collapsed to
    segment level.
    """
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
    segments, _ = model.transcribe(str(media_path), beam_size=5, word_timestamps=True)

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
