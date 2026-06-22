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

# Candidate locations for a .env file (first found wins)
_DEFAULT_DOTENV_PATHS = [
    Path.cwd() / ".env",
    Path.home() / ".config" / "quotegif" / ".env",
    Path.home() / ".quotegif.env",
]


def load_dotenv(dotenv_path: Path | None = None) -> None:
    """
    Load a .env file into os.environ using python-dotenv.
    Existing environment variables are never overwritten (override=False).
    Searches _DEFAULT_DOTENV_PATHS when no explicit path is given.
    Silently skips if python-dotenv is not installed or no file is found.
    """
    try:
        from dotenv import load_dotenv as _load  # type: ignore[import]
    except ImportError:
        return

    if dotenv_path is not None:
        _load(dotenv_path, override=False)
        return

    env_var = os.environ.get("QUOTEGIF_DOTENV")
    if env_var:
        _load(Path(env_var), override=False)
        return

    for candidate in _DEFAULT_DOTENV_PATHS:
        if candidate.exists():
            _load(candidate, override=False)
            return


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

    # media_folders: TOML list, or QUOTEGIF_MEDIA_FOLDERS as OS path separator-joined string
    if "media_folders" in raw:
        cfg.media_folders = [_expand_path(f) for f in raw["media_folders"]]
    elif env_folders := os.environ.get("QUOTEGIF_MEDIA_FOLDERS"):
        cfg.media_folders = [_expand_path(f) for f in env_folders.split(os.pathsep)]

    if "output_dir" in raw:
        cfg.output_dir = _expand_path(raw["output_dir"])
    elif env_out := os.environ.get("QUOTEGIF_OUTPUT_DIR"):
        cfg.output_dir = _expand_path(env_out)

    if "pad_before" in raw:
        cfg.pad_before = float(raw["pad_before"])
    elif v := os.environ.get("QUOTEGIF_PAD_BEFORE"):
        cfg.pad_before = float(v)

    if "pad_after" in raw:
        cfg.pad_after = float(raw["pad_after"])
    elif v := os.environ.get("QUOTEGIF_PAD_AFTER"):
        cfg.pad_after = float(v)

    if "max_duration" in raw:
        cfg.max_duration = float(raw["max_duration"])
    elif v := os.environ.get("QUOTEGIF_MAX_DURATION"):
        cfg.max_duration = float(v)

    if "gif" in raw:
        g = raw["gif"]
        cfg.gif = GifSettings(
            fps=int(g.get("fps", os.environ.get("QUOTEGIF_GIF_FPS", cfg.gif.fps))),
            width=int(g.get("width", os.environ.get("QUOTEGIF_GIF_WIDTH", cfg.gif.width))),
        )
    else:
        if v := os.environ.get("QUOTEGIF_GIF_FPS"):
            cfg.gif.fps = int(v)
        if v := os.environ.get("QUOTEGIF_GIF_WIDTH"):
            cfg.gif.width = int(v)

    # Provider selection
    provider_name = os.environ.get("QUOTEGIF_PROVIDER")
    if "provider" in raw:
        p = raw["provider"]
        cfg.provider.name = p.get("name", provider_name or cfg.provider.name)

        if "openai" in p:
            oa = p["openai"]
            cfg.provider.openai = OpenAISettings(
                model=oa.get("model", os.environ.get("OPENAI_MODEL", cfg.provider.openai.model)),
                api_key=oa.get("api_key") or os.environ.get("OPENAI_API_KEY"),
            )
        else:
            cfg.provider.openai = OpenAISettings(
                model=os.environ.get("OPENAI_MODEL", cfg.provider.openai.model),
                api_key=os.environ.get("OPENAI_API_KEY"),
            )

        if "anthropic" in p:
            an = p["anthropic"]
            cfg.provider.anthropic = AnthropicSettings(
                model=an.get("model", os.environ.get("ANTHROPIC_MODEL", cfg.provider.anthropic.model)),
                api_key=an.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"),
                search_api_key=an.get("search_api_key") or os.environ.get("TAVILY_API_KEY"),
            )
        else:
            cfg.provider.anthropic = AnthropicSettings(
                model=os.environ.get("ANTHROPIC_MODEL", cfg.provider.anthropic.model),
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
                search_api_key=os.environ.get("TAVILY_API_KEY"),
            )

        if "ollama" in p:
            ol = p["ollama"]
            cfg.provider.ollama = OllamaSettings(
                model=ol.get("model", os.environ.get("OLLAMA_MODEL", cfg.provider.ollama.model)),
                host=ol.get("host", os.environ.get("OLLAMA_HOST", cfg.provider.ollama.host)),
            )
        else:
            cfg.provider.ollama = OllamaSettings(
                model=os.environ.get("OLLAMA_MODEL", cfg.provider.ollama.model),
                host=os.environ.get("OLLAMA_HOST", cfg.provider.ollama.host),
            )
    else:
        # No [provider] TOML section — read everything from env
        cfg.provider.name = provider_name or cfg.provider.name
        cfg.provider.openai = OpenAISettings(
            model=os.environ.get("OPENAI_MODEL", cfg.provider.openai.model),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
        cfg.provider.anthropic = AnthropicSettings(
            model=os.environ.get("ANTHROPIC_MODEL", cfg.provider.anthropic.model),
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            search_api_key=os.environ.get("TAVILY_API_KEY"),
        )
        cfg.provider.ollama = OllamaSettings(
            model=os.environ.get("OLLAMA_MODEL", cfg.provider.ollama.model),
            host=os.environ.get("OLLAMA_HOST", cfg.provider.ollama.host),
        )

    if "whisper" in raw:
        w = raw["whisper"]
        cfg.whisper = WhisperSettings(
            enabled=bool(w.get("enabled", _env_bool("QUOTEGIF_WHISPER_ENABLED", cfg.whisper.enabled))),
            model=w.get("model", os.environ.get("QUOTEGIF_WHISPER_MODEL", cfg.whisper.model)),
            device=w.get("device", os.environ.get("QUOTEGIF_WHISPER_DEVICE", cfg.whisper.device)),
        )
    else:
        cfg.whisper = WhisperSettings(
            enabled=_env_bool("QUOTEGIF_WHISPER_ENABLED", cfg.whisper.enabled),
            model=os.environ.get("QUOTEGIF_WHISPER_MODEL", cfg.whisper.model),
            device=os.environ.get("QUOTEGIF_WHISPER_DEVICE", cfg.whisper.device),
        )

    return cfg


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.lower() not in ("0", "false", "no", "off")


def load_config(path: Path | None = None, dotenv_path: Path | None = None) -> AppConfig:
    """
    Load config from a .env file, a TOML config file, and environment variables.

    Resolution order (later sources win):
      1. Defaults baked into AppConfig dataclass fields
      2. .env file  (QUOTEGIF_DOTENV env var > explicit dotenv_path > _DEFAULT_DOTENV_PATHS)
      3. TOML config file  (--config flag > QUOTEGIF_CONFIG env var > _DEFAULT_CONFIG_PATHS)
      4. Direct CLI flags applied by cli.py after this function returns
    """
    load_dotenv(dotenv_path)
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
