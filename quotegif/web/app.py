from __future__ import annotations

import json
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
from quotegif.web.db import (
    authenticate,
    bootstrap_user_from_env,
    create_completed_edit_history,
    get_find_history,
    get_user_id,
    init_db,
    list_find_history,
    user_count,
)
from quotegif.web.edit_preview import create_preview_path, get_preview_path, register_preview
from quotegif.web.paths import is_path_in_user_output, resolve_user_output_file, user_output_dir
from quotegif.web.trim import build_trim_output_path, probe_duration, trim_media

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
    max_duration: float | None = Field(default=None, ge=1)
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


class TrimRequest(BaseModel):
    trim_start: float = Field(ge=0, description="Seconds from the start of the source file")
    trim_end: float = Field(gt=0, description="Seconds from the start of the source file")


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


def _safe_output_path(output_path: str, username: str) -> Path:
    try:
        return resolve_user_output_file(Path(output_path), username)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Output file not found") from e


def _history_row_to_dict(row, *, username: str) -> dict[str, Any]:
    params = json.loads(row["params_json"])
    item: dict[str, Any] = {
        "id": row["id"],
        "quote": row["quote"],
        "status": row["status"],
        "output_format": row["output_format"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "show": params.get("show"),
        "episode": params.get("episode"),
        "movie": params.get("movie"),
    }
    parent_id = row["parent_id"] if "parent_id" in row.keys() else None
    if parent_id:
        item["parent_id"] = parent_id
    edit = params.get("edit")
    if edit:
        item["edit"] = edit
        item["edit_summary"] = edit.get("summary") or (
            f"Trimmed {edit.get('trim_start', 0):.1f}s–{edit.get('trim_end', 0):.1f}s"
        )
    if row["status"] == "completed" and row["output_path"]:
        item["output_path"] = row["output_path"]
        item["output_url"] = f"/api/history/{row['id']}/output"
        item["download_url"] = f"/api/history/{row['id']}/output?download=1"
        item["can_edit"] = True
    return item


def _find_params_from_request(body: FindRequest) -> CliFindParams:
    return CliFindParams(
        quote=body.quote.strip(),
        pad_before=body.pad_before,
        pad_after=body.pad_after,
        max_duration=body.max_duration,
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
        "output_dir": str(user_output_dir(user)),
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
        "media_folders": [str(p) for p in cfg.media_folders],
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
    path = _safe_output_path(job.output_path, user)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/api/history")
def get_history(
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    user_id = get_user_id(user)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    rows = list_find_history(user_id, limit=limit)
    return {
        "items": [_history_row_to_dict(row, username=user) for row in rows],
        "output_dir": str(user_output_dir(user)),
    }


@app.get("/api/history/{history_id}/output")
def get_history_output(
    history_id: str,
    user: CurrentUser,
    download: Annotated[int, Query()] = 0,
) -> FileResponse:
    user_id = get_user_id(user)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    row = get_find_history(history_id, user_id)
    if not row or row["status"] != "completed" or not row["output_path"]:
        raise HTTPException(status_code=404, detail="Output not found")

    path = _safe_output_path(row["output_path"], user)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


def _format_secs(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}:{s:04.1f}"


def _history_source_for_edit(row, username: str) -> tuple[Path, float, str]:
    if row["status"] != "completed" or not row["output_path"]:
        raise HTTPException(status_code=400, detail="Only completed clips can be edited")
    path = _safe_output_path(row["output_path"], username)
    duration = probe_duration(path)
    output_format = row["output_format"] or (
        "gif" if path.suffix.lower() == ".gif" else "clip"
    )
    return path, duration, output_format


def _validate_trim_range(duration: float, trim_start: float, trim_end: float) -> None:
    if trim_end > duration + 0.05:
        raise HTTPException(
            status_code=400,
            detail=f"trim_end ({trim_end:.1f}s) exceeds clip duration ({duration:.1f}s)",
        )
    if trim_end <= trim_start:
        raise HTTPException(status_code=400, detail="trim_end must be greater than trim_start")
    if trim_end - trim_start < 0.1:
        raise HTTPException(status_code=400, detail="Selection must be at least 0.1 seconds")


def _build_trim_params(
    parent_row,
    *,
    source_path: Path,
    source_duration: float,
    trim_start: float,
    trim_end: float,
    output_format: str,
) -> dict[str, Any]:
    parent_params = json.loads(parent_row["params_json"])
    summary = (
        f"Trimmed {_format_secs(trim_start)}–{_format_secs(trim_end)} "
        f"(was {_format_secs(source_duration)})"
    )
    return {
        **{k: parent_params.get(k) for k in ("show", "episode", "movie", "output_format")},
        "output_format": output_format,
        "source": "edit",
        "edit": {
            "kind": "trim",
            "parent_id": parent_row["id"],
            "source_path": str(source_path),
            "source_duration": source_duration,
            "trim_start": trim_start,
            "trim_end": trim_end,
            "summary": summary,
        },
    }


@app.get("/api/history/{history_id}/edit")
def get_history_edit_info(history_id: str, user: CurrentUser) -> dict[str, Any]:
    user_id = get_user_id(user)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    row = get_find_history(history_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="History entry not found")

    path, duration, output_format = _history_source_for_edit(row, user)
    params = json.loads(row["params_json"])

    return {
        "id": row["id"],
        "quote": row["quote"],
        "duration": duration,
        "output_format": output_format,
        "source_url": f"/api/history/{history_id}/output",
        "filename": path.name,
        "parent_id": row["parent_id"] if "parent_id" in row.keys() else None,
        "show": params.get("show"),
        "episode": params.get("episode"),
    }


@app.post("/api/history/{history_id}/trim/preview")
def preview_history_trim(
    history_id: str,
    body: TrimRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    user_id = get_user_id(user)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    row = get_find_history(history_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="History entry not found")

    source, duration, output_format = _history_source_for_edit(row, user)
    _validate_trim_range(duration, body.trim_start, body.trim_end)

    out_dir = user_output_dir(user)
    token, preview_path = create_preview_path(out_dir, source.suffix)
    try:
        trim_media(source, preview_path, body.trim_start, body.trim_end)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    register_preview(token, user, preview_path)
    return {
        "preview_token": token,
        "preview_url": f"/api/edit-preview/{token}",
        "trim_start": body.trim_start,
        "trim_end": body.trim_end,
        "duration": body.trim_end - body.trim_start,
        "output_format": output_format,
    }


@app.post("/api/history/{history_id}/trim")
def save_history_trim(
    history_id: str,
    body: TrimRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    import uuid as uuid_mod

    user_id = get_user_id(user)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    row = get_find_history(history_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="History entry not found")

    source, duration, output_format = _history_source_for_edit(row, user)
    _validate_trim_range(duration, body.trim_start, body.trim_end)

    out_path = build_trim_output_path(source, body.trim_start, body.trim_end)
    try:
        trim_media(source, out_path, body.trim_start, body.trim_end)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    new_id = uuid_mod.uuid4().hex
    params = _build_trim_params(
        row,
        source_path=source,
        source_duration=duration,
        trim_start=body.trim_start,
        trim_end=body.trim_end,
        output_format=output_format,
    )
    create_completed_edit_history(
        new_id,
        user_id,
        row["quote"],
        params,
        str(out_path),
        output_format,
        parent_id=history_id,
    )

    new_row = get_find_history(new_id, user_id)
    assert new_row is not None
    return {
        "item": _history_row_to_dict(new_row, username=user),
        "message": params["edit"]["summary"],
    }


@app.get("/api/edit-preview/{token}")
def get_edit_preview(
    token: str,
    user: CurrentUser,
    download: Annotated[int, Query()] = 0,
) -> FileResponse:
    path = get_preview_path(token, user)
    if path is None:
        raise HTTPException(status_code=404, detail="Preview not found or expired")

    if not is_path_in_user_output(path, user):
        raise HTTPException(status_code=403, detail="Preview path is not accessible")

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
            "Set QUOTEGIF_WEB_USERNAME/PASSWORD or run: quotegif-web-create-user USER [PASSWORD]"
        )

    host = os.environ.get("QUOTEGIF_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("QUOTEGIF_WEB_PORT", "8765"))
    uvicorn.run(
        "quotegif.web.app:app",
        host=host,
        port=port,
        reload=os.environ.get("QUOTEGIF_WEB_RELOAD", "").lower() in ("1", "true"),
    )
