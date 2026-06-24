from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from quotegif.config import AppConfig, load_config
from quotegif.find_service import (
    FindError,
    FindInputRequired,
    FindParams,
    FindResult,
    _episode_ref_to_info,
    _media_entry_to_info,
    run_find,
)
from quotegif.models import EpisodeRef


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
    params: FindParams
    created_at: str
    updated_at: str
    progress_step: str | None = None
    progress_detail: str | None = None
    error: str | None = None
    input_kind: str | None = None
    input_message: str | None = None
    ref: EpisodeRef | None = None
    llm_candidates: list = field(default_factory=list)
    file_candidates: list = field(default_factory=list)
    cached_llm_refs: list[EpisodeRef] = field(default_factory=list)
    result: FindResult | None = None


_lock = threading.Lock()
_jobs: dict[str, JobRecord] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_to_dict(result: FindResult) -> dict[str, Any]:
    matched = None
    if result.matched:
        m = result.matched
        matched = {
            "start": m.start,
            "end": m.end,
            "text": m.text,
            "match_score": m.match_score,
            "match_query": m.match_query,
            "transcript_source": m.transcript_source,
            "clip_start": m.clip_start,
            "clip_end": m.clip_end,
            "clip_duration": m.clip_duration,
        }
    return {
        "ref": {
            "display": result.ref.display(),
            "title": result.ref.title,
            "media_type": result.ref.media_type,
            "season": result.ref.season,
            "episode": result.ref.episode,
            "episode_title": result.ref.episode_title,
            "confidence": result.ref.confidence,
            "reasoning": result.ref.reasoning,
        },
        "media_path": result.media_path,
        "pick_reason": result.pick_reason,
        "output_path": result.output_path,
        "output_format": result.output_format,
        "matched": matched,
        "llm_candidates": [
            {
                "display": c.display,
                "episode_title": c.episode_title,
                "exact_quote": c.exact_quote,
                "approx_timestamp": c.approx_timestamp,
                "confidence": c.confidence,
                "reasoning": c.reasoning,
                "season": c.season,
                "episode": c.episode,
            }
            for c in result.llm_candidates
        ],
        "timings": [
            {"name": s.name, "detail": s.detail, "seconds": s.seconds}
            for s in result.timings
        ],
        "total_seconds": result.total_seconds,
    }


def job_to_dict(job: JobRecord, *, output_url: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": job.id,
        "status": job.status.value,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress_step": job.progress_step,
        "progress_detail": job.progress_detail,
        "error": job.error,
        "params": {
            "quote": job.params.quote,
            "show": job.params.show,
            "episode": job.params.episode,
            "movie": job.params.movie,
            "output_format": job.params.output_format,
        },
    }
    if job.status == JobStatus.AWAITING_INPUT:
        payload["input"] = {
            "kind": job.input_kind,
            "message": job.input_message,
            "ref": (
                {
                    "display": job.ref.display(),
                    "confidence": job.ref.confidence,
                    "reasoning": job.ref.reasoning,
                }
                if job.ref
                else None
            ),
            "llm_candidates": job.llm_candidates,
            "file_candidates": job.file_candidates,
        }
    if job.status == JobStatus.COMPLETED and job.result:
        payload["result"] = _result_to_dict(job.result)
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


def _run_job(job_id: str, cfg: AppConfig) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        params = job.params

    def on_progress(step: str, detail: str | None = None) -> None:
        _update_job(job_id, progress_step=step, progress_detail=detail)

    _update_job(job_id, status=JobStatus.RUNNING)
    try:
        result = run_find(cfg, params, on_progress=on_progress)
        _update_job(
            job_id,
            status=JobStatus.COMPLETED,
            result=result,
            progress_step="done",
            progress_detail=None,
            error=None,
        )
    except FindInputRequired as e:
        llm_infos = [_episode_ref_to_info(r) for r in e.llm_candidates]
        file_infos = [_media_entry_to_info(m) for m in e.file_candidates]
        _update_job(
            job_id,
            status=JobStatus.AWAITING_INPUT,
            input_kind=e.kind,
            input_message=e.message,
            ref=e.ref,
            cached_llm_refs=list(e.llm_candidates),
            llm_candidates=[
                {
                    "display": c.display,
                    "episode_title": c.episode_title,
                    "confidence": c.confidence,
                    "reasoning": c.reasoning,
                    "season": c.season,
                    "episode": c.episode,
                }
                for c in llm_infos
            ],
            file_candidates=[
                {
                    "path": c.path,
                    "label": c.label,
                    "title": c.title,
                    "season": c.season,
                    "episode": c.episode,
                }
                for c in file_infos
            ],
            progress_step="awaiting_input",
            progress_detail=e.kind,
        )
    except FindError as e:
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            error=str(e),
            progress_step="failed",
        )
    except Exception as e:
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            error=f"Unexpected error: {e}",
            progress_step="failed",
        )


def create_job(params: FindParams, config_path: str | None = None) -> JobRecord:
    job_id = uuid.uuid4().hex
    now = _now_iso()
    job = JobRecord(
        id=job_id,
        status=JobStatus.QUEUED,
        params=params,
        created_at=now,
        updated_at=now,
    )
    with _lock:
        _jobs[job_id] = job

    cfg = load_config(config_path)

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, cfg),
        name=f"quotegif-find-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return job


def continue_job(
    job_id: str,
    *,
    auto_confirm: bool = False,
    media_path: str | None = None,
    llm_candidate_index: int | None = None,
    config_path: str | None = None,
) -> JobRecord | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.status != JobStatus.AWAITING_INPUT:
            return None
        params = job.params
        if auto_confirm:
            params.auto_confirm = True
        if media_path:
            params.media_path = media_path
        if llm_candidate_index is not None:
            params.llm_candidate_index = llm_candidate_index
        if job.cached_llm_refs:
            params.cached_llm_candidates = list(job.cached_llm_refs)
            if job.ref:
                params.resolved_ref = job.ref
        elif job.ref:
            params.cached_llm_candidates = [job.ref]
            params.resolved_ref = job.ref
        job.status = JobStatus.QUEUED
        job.updated_at = _now_iso()

    cfg = load_config(config_path)
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, cfg),
        name=f"quotegif-find-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return _jobs[job_id]


def get_job(job_id: str) -> JobRecord | None:
    with _lock:
        return _jobs.get(job_id)
