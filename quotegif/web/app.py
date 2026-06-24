from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from quotegif.config import check_ffmpeg, load_config
from quotegif.pipeline import OutputFormat
from quotegif.providers.registry import KNOWN_PROVIDERS, get_active_model
from quotegif.web import jobs
from quotegif.web.auth import CurrentUser, get_client_ip, require_user, session_secret
from quotegif.web.cli_runner import CliFindParams
from quotegif.web.db import authenticate, bootstrap_user_from_env, init_db, user_count

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="QuoteGif",
    description="Web UI for quotegif find (runs the CLI)",
    version="0.1.0",
)

app.add_middleware(SessionMiddleware, secret_key=session_secret(), https_only=False)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    bootstrap_user_from_env()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


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
    verbose: bool = False
    config_path: str | None = None
    media_path: str | None = None


class ContinueRequest(BaseModel):
    auto_confirm: bool = False
    media_path: str | None = None


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


def _find_params_from_request(body: FindRequest) -> CliFindParams:
    return CliFindParams(
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
        yes=body.auto_confirm,
        output_format=body.output_format,
        verbose=body.verbose,
        config_path=body.config_path,
        media_path=body.media_path,
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    ok, msg = check_ffmpeg()
    return {
        "ok": ok,
        "ffmpeg": msg,
        "users_configured": user_count() > 0,
    }


@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request) -> dict[str, str]:
    ip = get_client_ip(request)
    ok, message, retry_after = authenticate(body.username, body.password, ip)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS if retry_after else status.HTTP_401_UNAUTHORIZED,
            detail=message,
            headers={"Retry-After": str(retry_after)} if retry_after else None,
        )
    request.session["username"] = body.username.strip()
    return {"username": body.username.strip()}


@app.post("/api/auth/logout")
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: CurrentUser) -> dict[str, str]:
    return {"username": user}


@app.get("/api/config")
def get_config(user: CurrentUser) -> dict[str, Any]:
    cfg = load_config()
    return {
        "username": user,
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
def start_find(body: FindRequest, user: CurrentUser) -> dict[str, Any]:
    if not body.quote.strip():
        raise HTTPException(status_code=400, detail="quote is required")

    cfg = load_config()
    if not cfg.media_folders:
        raise HTTPException(
            status_code=400,
            detail="No media_folders configured. Set QUOTEGIF_MEDIA_FOLDERS or config.toml.",
        )

    params = _find_params_from_request(body)
    job = jobs.create_job(params, owner=user)
    return jobs.job_to_dict(job)


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str, user: CurrentUser) -> dict[str, Any]:
    job = jobs.get_job(job_id, owner=user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    output_url = None
    if job.status == jobs.JobStatus.COMPLETED and job.output_path:
        output_url = f"/api/jobs/{job_id}/output"

    return jobs.job_to_dict(job, output_url=output_url)


@app.post("/api/jobs/{job_id}/continue")
def continue_job(job_id: str, body: ContinueRequest, user: CurrentUser) -> dict[str, Any]:
    job = jobs.continue_job(
        job_id,
        owner=user,
        auto_confirm=body.auto_confirm,
        media_path=body.media_path,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not awaiting input")
    return jobs.job_to_dict(job)


@app.get("/api/jobs/{job_id}/output")
def get_job_output(
    job_id: str,
    user: CurrentUser,
    download: Annotated[int, Query()] = 0,
) -> FileResponse:
    job = jobs.get_job(job_id, owner=user)
    if not job or job.status != jobs.JobStatus.COMPLETED or not job.output_path:
        raise HTTPException(status_code=404, detail="Output not ready")

    cfg = load_config()
    path = _safe_output_path(job.output_path, cfg)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main() -> None:
    import uvicorn

    if user_count() == 0 and not (
        os.environ.get("QUOTEGIF_WEB_USERNAME") and os.environ.get("QUOTEGIF_WEB_PASSWORD")
    ):
        print(
            "Warning: no web users configured. "
            "Set QUOTEGIF_WEB_USERNAME/PASSWORD or run: quotegif-web-create-user USER"
        )

    host = os.environ.get("QUOTEGIF_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("QUOTEGIF_WEB_PORT", "8765"))
    uvicorn.run(
        "quotegif.web.app:app",
        host=host,
        port=port,
        reload=os.environ.get("QUOTEGIF_WEB_RELOAD", "").lower() in ("1", "true"),
    )
