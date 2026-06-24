from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from quotegif.config import check_ffmpeg, load_config
from quotegif.find_service import FindParams
from quotegif.pipeline import OutputFormat
from quotegif.providers.registry import KNOWN_PROVIDERS, get_active_model
from quotegif.web import jobs

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="QuoteGif",
    description="Web UI for finding quotes and rendering clips or GIFs",
    version="0.1.0",
)


class FindRequest(BaseModel):
    quote: str = Field(min_length=1)
    pad_before: float | None = Field(default=None, ge=0)
    pad_after: float | None = Field(default=None, ge=0)
    fps: int | None = Field(default=None, ge=1, le=60)
    width: int | None = Field(default=None, ge=120, le=1920)
    provider: str | None = None
    model: str | None = None
    show: str | None = None
    episode: str | None = None
    movie: bool = False
    candidates: int = Field(default=5, ge=1, le=10)
    around: str | None = None
    auto_confirm: bool = False
    output_format: OutputFormat = "gif"
    media_path: str | None = None
    llm_candidate_index: int | None = Field(default=None, ge=0)


class ContinueRequest(BaseModel):
    auto_confirm: bool = False
    media_path: str | None = None
    llm_candidate_index: int | None = Field(default=None, ge=0)


def _provider_status(cfg) -> list[dict[str, Any]]:
    rows = []
    for name in KNOWN_PROVIDERS:
        if name == "openai":
            configured = bool(cfg.provider.openai.api_key)
        elif name == "anthropic":
            configured = bool(cfg.provider.anthropic.api_key)
        else:
            configured = True
        rows.append({
            "name": name,
            "configured": configured,
            "model": get_active_model(cfg, name),
            "default": name == cfg.provider.name,
        })
    return rows


def _safe_output_path(output_path: str, cfg) -> Path:
    resolved = Path(output_path).resolve()
    allowed_roots = [
        cfg.output_dir.resolve(),
        Path.cwd().resolve(),
    ]
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            if resolved.is_file():
                return resolved
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="Output path is not accessible")


@app.get("/api/health")
def health() -> dict[str, Any]:
    ok, msg = check_ffmpeg()
    return {"ok": ok, "ffmpeg": msg}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    cfg = load_config()
    return {
        "media_folders": [str(p) for p in cfg.media_folders],
        "output_dir": str(cfg.output_dir),
        "pad_before": cfg.pad_before,
        "pad_after": cfg.pad_after,
        "max_duration": cfg.max_duration,
        "gif": {"fps": cfg.gif.fps, "width": cfg.gif.width},
        "whisper": {
            "enabled": cfg.whisper.enabled,
            "model": cfg.whisper.model,
            "clip_window": cfg.whisper.clip_window,
        },
        "provider": cfg.provider.name,
        "providers": _provider_status(cfg),
        "ffmpeg_ok": check_ffmpeg()[0],
    }


@app.post("/api/find")
def start_find(body: FindRequest) -> dict[str, Any]:
    if not body.quote.strip():
        raise HTTPException(status_code=400, detail="quote is required")

    cfg = load_config()
    if not cfg.media_folders:
        raise HTTPException(
            status_code=400,
            detail="No media_folders configured. Set QUOTEGIF_MEDIA_FOLDERS or config.toml.",
        )

    params = FindParams(
        quote=body.quote.strip(),
        pad_before=body.pad_before,
        pad_after=body.pad_after,
        fps=body.fps,
        width=body.width,
        provider=body.provider,
        model=body.model,
        show=body.show.strip() if body.show else None,
        episode=body.episode.strip() if body.episode else None,
        movie=body.movie,
        candidates=body.candidates,
        around=body.around,
        auto_confirm=body.auto_confirm,
        output_format=body.output_format,
        media_path=body.media_path,
        llm_candidate_index=body.llm_candidate_index,
    )

    job = jobs.create_job(params)
    return jobs.job_to_dict(job)


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str) -> dict[str, Any]:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    output_url = None
    if job.status == jobs.JobStatus.COMPLETED and job.result:
        output_url = f"/api/jobs/{job_id}/output"

    return jobs.job_to_dict(job, output_url=output_url)


@app.post("/api/jobs/{job_id}/continue")
def continue_job(job_id: str, body: ContinueRequest) -> dict[str, Any]:
    job = jobs.continue_job(
        job_id,
        auto_confirm=body.auto_confirm,
        media_path=body.media_path,
        llm_candidate_index=body.llm_candidate_index,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not awaiting input")
    return jobs.job_to_dict(job)


@app.get("/api/jobs/{job_id}/output")
def get_job_output(
    job_id: str,
    download: Annotated[int, Query()] = 0,
) -> FileResponse:
    job = jobs.get_job(job_id)
    if not job or job.status != jobs.JobStatus.COMPLETED or not job.result:
        raise HTTPException(status_code=404, detail="Output not ready")

    cfg = load_config()
    path = _safe_output_path(job.result.output_path, cfg)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    filename = path.name
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("QUOTEGIF_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("QUOTEGIF_WEB_PORT", "8765"))
    uvicorn.run(
        "quotegif.web.app:app",
        host=host,
        port=port,
        reload=os.environ.get("QUOTEGIF_WEB_RELOAD", "").lower() in ("1", "true"),
    )
