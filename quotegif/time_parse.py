from __future__ import annotations

import re


def parse_timestamp(text: str) -> float:
    """
    Parse a rough timestamp into seconds.

    Accepts:
      - seconds as a number: "3252", "3252.5"
      - MM:SS: "54:32"
      - HH:MM:SS: "1:32:05"
    """
    text = text.strip()
    if not text:
        raise ValueError("Empty timestamp")

    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)

    parts = text.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    raise ValueError(
        f'Invalid timestamp {text!r}. Use seconds, MM:SS, or HH:MM:SS (e.g. "54:32").'
    )


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:05.2f}".rstrip("0").rstrip(".")
    return f"{m}:{s:05.2f}".rstrip("0").rstrip(".")
