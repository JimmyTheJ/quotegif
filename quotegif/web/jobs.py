from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from quotegif.web.cli_runner import CliFindParams, CliNeedsInput, CliRunResult, run_find_cli


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobRecord:
    id: str
    status: JobStatus
    params: CliFindParams
    owner: str
    created_at: str
    updated_at: str
    progress_step: str | None = None
    progress_detail: str | None = None
    error: str | None = None
    input_kind: str | None = None
    input_message: str | None = None
    input_ref: dict | None = None
    file_candidates: list[dict] = field(default_factory=list)
    output_path: str | None = None
    output_format: str | None = None
    log_tail: list[str] = field(default_factory=list)
    cli_command: str | None = None


_lock = threading.Lock()
_jobs: dict[str, JobRecord] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_to_dict(job: JobRecord, *, output_url: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": job.id,
        "status": job.status.value,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress_step": job.progress_step,
        "progress_detail": job.progress_detail,
        "error": job.error,
        "cli_command": job.cli_command,
        "params": {
            "quote": job.params.quote,
            "show": job.params.show,
            "episode": job.params.episode,
            "movie": job.params.movie,
            "output_format": job.params.output_format,
        },
        "log_tail": job.log_tail[-40:],
    }
    if job.status == JobStatus.AWAITING_INPUT:
        payload["input"] = {
            "kind": job.input_kind,
            "message": job.input_message,
            "ref": job.input_ref,
            "file_candidates": job.file_candidates,
        }
    if job.status == JobStatus.COMPLETED and job.output_path:
        payload["result"] = {
            "output_path": job.output_path,
            "output_format": job.output_format,
        }
        if output_url:
            payload["result"]["output_url"] = output_url
            payload["result"]["download_url"] = f"{output_url}?download=1"
    return payload


def _update_job(job_id: str, **kwargs: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        for key, value in kwargs.items():
            setattr(job, key, value)
        job.updated_at = _now_iso()


def _apply_needs_input(job_id: str, needs: CliNeedsInput) -> None:
    _update_job(
        job_id,
        status=JobStatus.AWAITING_INPUT,
        input_kind=needs.kind,
        input_message=needs.message,
        input_ref=needs.ref,
        file_candidates=needs.file_candidates,
        progress_step="awaiting_input",
        progress_detail=needs.kind,
        error=None,
    )


def _run_job(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        params = job.params

    def on_progress(step: str, detail: str | None = None) -> None:
        if step == "cli" and detail:
            _update_job(job_id, cli_command=detail, progress_step="running", progress_detail=detail)
        elif step == "log" and detail:
            with _lock:
                current = _jobs.get(job_id)
                if current:
                    tail = list(current.log_tail[-39:]) + [detail]
                    current.log_tail = tail
                    current.progress_detail = detail[:200]
                    current.updated_at = _now_iso()

    _update_job(job_id, status=JobStatus.RUNNING, progress_step="running")
    result = run_find_cli(params, on_progress=on_progress)

    if result.needs_input:
        _apply_needs_input(job_id, result.needs_input)
        _update_job(job_id, log_tail=result.log_lines[-40:])
        return

    if result.exit_code != 0 or not result.output_path:
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            error=result.error or "quotegif find failed",
            log_tail=result.log_lines[-40:],
            progress_step="failed",
        )
        return

    _update_job(
        job_id,
        status=JobStatus.COMPLETED,
        output_path=result.output_path,
        output_format=result.output_format,
        log_tail=result.log_lines[-40:],
        progress_step="done",
        progress_detail=None,
        error=None,
    )


def create_job(params: CliFindParams, *, owner: str) -> JobRecord:
    job_id = uuid.uuid4().hex
    now = _now_iso()
    job = JobRecord(
        id=job_id,
        status=JobStatus.QUEUED,
        params=params,
        owner=owner,
        created_at=now,
        updated_at=now,
    )
    with _lock:
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job_id,),
        name=f"quotegif-cli-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return job


def continue_job(
    job_id: str,
    *,
    owner: str,
    auto_confirm: bool = False,
    media_path: str | None = None,
) -> JobRecord | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.status != JobStatus.AWAITING_INPUT or job.owner != owner:
            return None
        params = job.params
        if auto_confirm:
            params.yes = True
        if media_path:
            params.media_path = media_path
        job.status = JobStatus.QUEUED
        job.updated_at = _now_iso()

    thread = threading.Thread(
        target=_run_job,
        args=(job_id,),
        name=f"quotegif-cli-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return _jobs[job_id]


def get_job(job_id: str, *, owner: str | None = None) -> JobRecord | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if owner is not None and job.owner != owner:
            return None
        return job
