from __future__ import annotations

import re
from pathlib import Path

from quotegif.config import load_config
from quotegif.web.db import get_user_by_username

_USER_DIR_RE = re.compile(r"^user_(\d+)$")


def base_output_dir() -> Path:
    return load_config().output_dir


def user_output_dir_for_id(user_id: int) -> Path:
    """Per-user output root: {QUOTEGIF_OUTPUT_DIR}/user_{id}/"""
    path = base_output_dir() / f"user_{user_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_output_dir(username: str) -> Path:
    user = get_user_by_username(username)
    if user is None:
        raise ValueError(f"Unknown user: {username}")
    return user_output_dir_for_id(int(user["id"]))


def ensure_user_output_dir(username: str) -> Path:
    return user_output_dir(username)


def is_path_in_user_output(path: Path, username: str) -> bool:
    """True when path resolves inside the user's dedicated output folder."""
    user_dir = user_output_dir(username).resolve()
    try:
        path.resolve().relative_to(user_dir)
        return True
    except ValueError:
        return False


def resolve_user_output_file(path: Path, username: str) -> Path:
    """Resolve path and ensure it is a file inside the user's output directory."""
    resolved = path.resolve()
    if not is_path_in_user_output(resolved, username):
        raise PermissionError("Output path is outside your directory")
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved
