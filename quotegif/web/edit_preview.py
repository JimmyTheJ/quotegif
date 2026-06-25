from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class PreviewRecord:
    owner: str
    path: Path
    created_at: datetime


_lock = threading.Lock()
_previews: dict[str, PreviewRecord] = {}


def previews_dir(user_output_dir: Path) -> Path:
    path = user_output_dir / ".previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_preview_path(user_output_dir: Path, suffix: str) -> tuple[str, Path]:
    token = uuid.uuid4().hex
    out_path = previews_dir(user_output_dir) / f"{token}{suffix}"
    return token, out_path


def register_preview(token: str, owner: str, path: Path) -> None:
    with _lock:
        _previews[token] = PreviewRecord(
            owner=owner,
            path=path,
            created_at=datetime.now(timezone.utc),
        )


def get_preview_path(token: str, owner: str) -> Path | None:
    with _lock:
        record = _previews.get(token)
        if not record or record.owner != owner:
            return None
        if not record.path.is_file():
            return None
        return record.path
