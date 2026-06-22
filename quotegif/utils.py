from __future__ import annotations

import re
import unicodedata


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v",
    ".ts", ".mpg", ".mpeg", ".flv", ".webm",
}


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation/accents, collapse whitespace."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def seconds_to_timestamp(secs: float) -> str:
    """Format seconds as HH:MM:SS.mmm."""
    secs = max(0.0, secs)
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def sanitize_filename(name: str) -> str:
    """Make a string safe for use in a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:100]
