from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

_DEFAULT_CONFIG_PATHS = [
    Path.home() / ".config" / "quotegif" / "config.toml",
    Path.home() / ".quotegif.toml",
]


@dataclass
class GifSettings:
    fps: int = 12
    width: int = 480


@dataclass
class OpenAISettings:
    model: str = "gpt-4o"
    api_key: str | None = None


@dataclass
class AnthropicSettings:
    model: str = "claude-3-5-sonnet-20241022"
    api_key: str | None = None
    search_api_key: str | None = None


@dataclass
class OllamaSettings:
    model: str = "llama3.1"
    host: str = "http://localhost:11434"


@dataclass
class ProviderSettings:
    name: str = "openai"
    openai: OpenAISettings = field(default_factory=OpenAISettings)
    anthropic: AnthropicSettings = field(default_factory=AnthropicSettings)
    ollama: OllamaSettings = field(default_factory=OllamaSettings)


@dataclass
class WhisperSettings:
    enabled: bool = True
    model: str = "base"
    device: str = "auto"


@dataclass
class AppConfig:
    media_folders: list[Path] = field(default_factory=list)
    output_dir: Path = field(default_factory=lambda: Path.home() / "quotegifs")
    pad_before: float = 1.5
    pad_after: float = 2.5
    max_duration: float = 12.0
    gif: GifSettings = field(default_factory=GifSettings)
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    whisper: WhisperSettings = field(default_factory=WhisperSettings)


def _merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


def _expand_path(p: str | Path) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser()


def _build_config(raw: dict[str, Any]) -> AppConfig:
    cfg = AppConfig()

    if "media_folders" in raw:
        cfg.media_folders = [_expand_path(f) for f in raw["media_folders"]]

    if "output_dir" in raw:
        cfg.output_dir = _expand_path(raw["output_dir"])

    if "pad_before" in raw:
        cfg.pad_before = float(raw["pad_before"])
    if "pad_after" in raw:
        cfg.pad_after = float(raw["pad_after"])
    if "max_duration" in raw:
        cfg.max_duration = float(raw["max_duration"])

    if "gif" in raw:
        g = raw["gif"]
        cfg.gif = GifSettings(
            fps=int(g.get("fps", cfg.gif.fps)),
            width=int(g.get("width", cfg.gif.width)),
        )

    if "provider" in raw:
        p = raw["provider"]
        cfg.provider.name = p.get("name", cfg.provider.name)

        if "openai" in p:
            oa = p["openai"]
            cfg.provider.openai = OpenAISettings(
                model=oa.get("model", cfg.provider.openai.model),
                api_key=oa.get("api_key") or os.environ.get("OPENAI_API_KEY"),
            )
        else:
            cfg.provider.openai.api_key = (
                cfg.provider.openai.api_key or os.environ.get("OPENAI_API_KEY")
            )

        if "anthropic" in p:
            an = p["anthropic"]
            cfg.provider.anthropic = AnthropicSettings(
                model=an.get("model", cfg.provider.anthropic.model),
                api_key=an.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"),
                search_api_key=an.get("search_api_key") or os.environ.get("TAVILY_API_KEY"),
            )
        else:
            cfg.provider.anthropic.api_key = (
                cfg.provider.anthropic.api_key or os.environ.get("ANTHROPIC_API_KEY")
            )
            cfg.provider.anthropic.search_api_key = (
                cfg.provider.anthropic.search_api_key or os.environ.get("TAVILY_API_KEY")
            )

        if "ollama" in p:
            ol = p["ollama"]
            cfg.provider.ollama = OllamaSettings(
                model=ol.get("model", cfg.provider.ollama.model),
                host=ol.get("host", cfg.provider.ollama.host),
            )

    if "whisper" in raw:
        w = raw["whisper"]
        cfg.whisper = WhisperSettings(
            enabled=bool(w.get("enabled", cfg.whisper.enabled)),
            model=w.get("model", cfg.whisper.model),
            device=w.get("device", cfg.whisper.device),
        )

    return cfg


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from file + environment variables."""
    raw: dict[str, Any] = {}

    config_path = path
    if config_path is None:
        env_path = os.environ.get("QUOTEGIF_CONFIG")
        if env_path:
            config_path = Path(env_path)
        else:
            for candidate in _DEFAULT_CONFIG_PATHS:
                if candidate.exists():
                    config_path = candidate
                    break

    if config_path is not None and config_path.exists():
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    cfg = _build_config(raw)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def check_ffmpeg() -> tuple[bool, str]:
    """Return (ok, message) indicating whether ffmpeg and ffprobe are available."""
    missing = []
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            missing.append(tool)
    if missing:
        return False, f"Missing required tools: {', '.join(missing)}. Install ffmpeg and ensure it is on PATH."
    return True, "ffmpeg and ffprobe found."
