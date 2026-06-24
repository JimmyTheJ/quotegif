from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from quotegif.pipeline import OutputFormat

OUTPUT_PREFIX = "QUOTEGIF_OUTPUT:"
NEEDS_INPUT_PREFIX = "QUOTEGIF_NEEDS_INPUT:"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class CliFindParams:
    """All parameters accepted by `quotegif find`."""

    quote: str
    pad_before: float | None = None
    pad_after: float | None = None
    fps: int | None = None
    width: int | None = None
    provider: str | None = None
    model: str | None = None
    show: str | None = None
    episode: str | None = None
    movie: bool = False
    candidates: int = 5
    around: str | None = None
    yes: bool = False
    output_format: OutputFormat = "gif"
    verbose: bool = False
    config_path: str | None = None
    media_path: str | None = None


@dataclass
class CliNeedsInput:
    kind: str
    message: str
    ref: dict | None = None
    file_candidates: list[dict] = field(default_factory=list)


@dataclass
class CliRunResult:
    exit_code: int
    output_path: str | None = None
    output_format: OutputFormat | None = None
    needs_input: CliNeedsInput | None = None
    error: str | None = None
    log_lines: list[str] = field(default_factory=list)


ProgressCallback = Callable[[str, str | None], None]


def _quotegif_executable() -> list[str]:
    return [sys.executable, "-m", "quotegif", "find"]


def build_find_argv(params: CliFindParams) -> list[str]:
    cmd = _quotegif_executable() + [params.quote]

    if params.pad_before is not None:
        cmd += ["--pad-before", str(params.pad_before)]
    if params.pad_after is not None:
        cmd += ["--pad-after", str(params.pad_after)]
    if params.fps is not None:
        cmd += ["--fps", str(params.fps)]
    if params.width is not None:
        cmd += ["--width", str(params.width)]
    if params.provider:
        cmd += ["--provider", params.provider]
    if params.model:
        cmd += ["--model", params.model]
    if params.show:
        cmd += ["--show", params.show]
    if params.episode:
        cmd += ["--episode", params.episode]
    if params.movie:
        cmd.append("--movie")
    if params.candidates != 5:
        cmd += ["--candidates", str(params.candidates)]
    if params.around:
        cmd += ["--around", params.around]
    if params.yes:
        cmd.append("--yes")
    if params.output_format != "gif":
        cmd += ["--format", params.output_format]
    if params.verbose:
        cmd.append("--verbose")
    if params.config_path:
        cmd += ["--config", params.config_path]
    if params.media_path:
        cmd += ["--media-path", params.media_path]

    cmd.append("--print-output")
    return cmd


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _parse_line(line: str) -> tuple[str | None, CliNeedsInput | None]:
    clean = _strip_ansi(line).strip()
    if clean.startswith(OUTPUT_PREFIX):
        return clean[len(OUTPUT_PREFIX) :].strip(), None
    if clean.startswith(NEEDS_INPUT_PREFIX):
        raw = clean[len(NEEDS_INPUT_PREFIX) :]
        data = json.loads(raw)
        return None, CliNeedsInput(
            kind=data.get("kind", "unknown"),
            message=data.get("message", ""),
            ref=data.get("ref"),
            file_candidates=data.get("file_candidates", []),
        )
    return None, None


def _tail_error(log_lines: list[str], max_lines: int = 12) -> str:
    useful = [
        _strip_ansi(line).strip()
        for line in log_lines
        if _strip_ansi(line).strip()
    ]
    if not useful:
        return "quotegif find failed"
    return "\n".join(useful[-max_lines:])


def run_find_cli(
    params: CliFindParams,
    *,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> CliRunResult:
    """Run `quotegif find` as a subprocess with full parameter parity."""
    cmd = build_find_argv(params)
    env = os.environ.copy()
    env["QUOTEGIF_NONINTERACTIVE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    if on_progress:
        on_progress("cli", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )

    log_lines: list[str] = []
    output_path: str | None = None
    needs_input: CliNeedsInput | None = None

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        if cancel_event and cancel_event.is_set():
            proc.kill()
            break
        line = raw_line.rstrip("\n")
        log_lines.append(line)
        if on_progress:
            clean = _strip_ansi(line).strip()
            if clean:
                on_progress("log", clean[:500])
        parsed_path, parsed_input = _parse_line(line)
        if parsed_path:
            output_path = parsed_path
        if parsed_input:
            needs_input = parsed_input

    exit_code = proc.wait()

    if needs_input or exit_code == 2:
        return CliRunResult(
            exit_code=exit_code,
            needs_input=needs_input,
            log_lines=log_lines,
            error=needs_input.message if needs_input else _tail_error(log_lines),
        )

    if exit_code != 0:
        return CliRunResult(
            exit_code=exit_code,
            error=_tail_error(log_lines),
            log_lines=log_lines,
        )

    if not output_path:
        return CliRunResult(
            exit_code=1,
            error="CLI finished without QUOTEGIF_OUTPUT line",
            log_lines=log_lines,
        )

    return CliRunResult(
        exit_code=0,
        output_path=output_path,
        output_format=params.output_format,
        log_lines=log_lines,
    )
