# quotegif

Turn a vague half-remembered quote into a clip or subtitled GIF of the exact moment.

```
quotegif find "the one where he says something about soup"
```

QuoteGif uses an LLM with web search to identify the show, season, and episode from your quote, locates the file in your local media library, finds the exact timestamp via subtitles (with Whisper transcription as fallback), and renders either a **video clip with audio** or a **silent GIF with burned-in subtitles**.

---

## Requirements

- Python 3.11+
- **ffmpeg** and **ffprobe** on your `PATH` ([download](https://ffmpeg.org/download.html))
- An API key for at least one LLM provider (OpenAI, Anthropic, or a local Ollama instance)

---

## Installation

```bash
# Core + OpenAI provider (recommended)
pip install "quotegif[openai]"

# Core + Anthropic provider (uses Tavily for web search)
pip install "quotegif[anthropic]"

# Core + local Ollama provider (no web search; uses model knowledge)
pip install "quotegif[ollama]"

# Everything including Whisper transcription fallback
pip install "quotegif[all]"
```

---

## Quick start

### 1. Configure via `.env` (recommended)

Copy `.env.example` to `.env` in the project directory (or `~/.config/quotegif/.env`) and fill in your values:

```bash
cp .env.example .env
```

```dotenv
OPENAI_API_KEY=sk-...
QUOTEGIF_MEDIA_FOLDERS=D:\Videos\TV;D:\Videos\Movies
QUOTEGIF_OUTPUT_DIR=D:\quotegifs
```

Variables in `.env` are loaded automatically. They do **not** override values already set in your shell.

### 2. Or set environment variables directly

```bash
# OpenAI
export OPENAI_API_KEY=sk-...

# Anthropic (also needs Tavily for web search)
export ANTHROPIC_API_KEY=sk-ant-...
export TAVILY_API_KEY=tvly-...

# Media folders (OS path separator: ; on Windows, : on Unix)
export QUOTEGIF_MEDIA_FOLDERS="/TV Shows:/Movies"
```

### 3. Or use a TOML config file (optional, for more complex setups)

Copy `config.example.toml` to `~/.config/quotegif/config.toml` and edit it:

```toml
media_folders = [
    "/path/to/TV Shows",
    "/path/to/Movies",
]
output_dir = "~/quotegifs"

[provider]
name = "openai"
```

### 3. Index your library

```bash
quotegif index
```

### 4. Find a quote

```bash
quotegif find "that's what she said"
```

---

## Docker / Docker Compose

Docker packages Python, ffmpeg, and all dependencies so you don't need to install anything locally.

### How it works

This is a CLI tool, not a server. Docker Compose doesn't run a persistent container you send commands to — instead, each command spins up a fresh container, runs, and exits:

```bash
docker compose run --rm quotegif find "no soup for you"
#                  ^^^^^^^^^^^^^^^^ boilerplate every time
```

To avoid typing all that, use the included wrapper script:

```bash
# Linux / macOS
./qg find "no soup for you"

# Windows
qg find "no soup for you"
```

`qg` (or `qg.bat` on Windows) just expands to the `docker compose run --rm quotegif` prefix. To use it from anywhere, add the project directory to your `PATH`, or symlink it:

```bash
# Linux / macOS — make it available system-wide
chmod +x qg
ln -s "$PWD/qg" /usr/local/bin/quotegif
```

### Setup

**1. Configure `.env`**

```bash
cp .env.example .env
```

Edit `.env` — at minimum set these (`.env` is gitignored, nothing is committed):

```dotenv
# Where your video files live on the HOST
QUOTEGIF_HOST_MEDIA=/path/to/your/video/library

# Where GIFs are written on the HOST
QUOTEGIF_HOST_OUTPUT=/path/to/where/gifs/are/saved

# API key for whichever provider you're using
OPENAI_API_KEY=sk-...
```

Docker Compose reads `.env` automatically for both variable substitution (the volume paths) and passing keys into the container. If either host path is missing, compose will refuse to start with a clear error message.

**3. Build and index**

```bash
docker compose build
./qg index        # scan your media library (only needed once, or after adding files)
./qg config       # verify everything looks right
```

### Usage

```bash
./qg find "no soup for you"
./qg find "no soup for you" --format clip
./qg find "that's what she said" --pad-before 2 --width 640
./qg find "no soup for you" --episode "Seinfeld S07E06"   # skip LLM, you know the episode
./qg find "no soup for you" --provider ollama              # use local model
./qg find "no soup for you" --model gpt-4o-mini            # cheaper OpenAI model
./qg compare "no soup for you" --providers openai,ollama   # compare two providers
```

### GPU variant (faster Whisper)

If you have an NVIDIA GPU and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed:

```bash
docker compose --profile gpu run --rm quotegif-gpu find "winter is coming"
```

This uses `QUOTEGIF_WHISPER_DEVICE=cuda` automatically.

### Volume layout

| Container path | Purpose | Configured in |
|----------------|---------|---------------|
| `/media` | Your video files (read-only) | `docker-compose.yml` → `volumes.media.driver_opts.device` |
| `/output` | GIF output (read-write) | `docker-compose.yml` → `volumes.output.driver_opts.device` |
| `quotegif-index` (named) | Library index cache | Docker-managed |
| `quotegif-whisper` (named) | Whisper model weights | Docker-managed |

> **Multiple media folders:** Point the `media` volume's `device` at a single parent directory and organise subdirectories under it (`TV/`, `Movies/`, etc.). The container indexes `/media` recursively.

---

## Commands

### `quotegif find <quote>`

Identify the quote's source, find it in your library, and render output.

**Output formats (`--format`):**

| Format | What you get |
|--------|----------------|
| `gif` (default) | Silent animated GIF with **all dialogue in the clip burned in as subtitles**. Uses existing subs if present; otherwise Whisper generates them. |
| `clip` | Video clip with **full audio**, same container/codec as the source when possible. Best when you want to hear the quote. |

```
Options:
  --format     clip | gif   Output type (default: gif)
  --pad-before FLOAT   Seconds before the quote starts (default: 1.5)
  --pad-after  FLOAT   Seconds after the quote ends   (default: 2.5)
  --fps        INT     GIF frames per second           (default: 12)
  --width      INT     GIF pixel width                 (default: 480)
  --provider   TEXT    Override LLM provider (openai|anthropic|ollama)
  --model      TEXT    Override model for the provider
  --episode    TEXT    Skip LLM; specify episode directly e.g. "The Office S03E14"
  --yes / -y           Auto-confirm low-confidence and ambiguous matches
  --open               Open the output file after creation
  --config     PATH    Path to config TOML file
```

**Examples:**

```bash
# Subtitled GIF (default) — quote is readable without audio
quotegif find "no soup for you"

# Video clip with audio — hear the quote in context
quotegif find "no soup for you" --format clip

# You know which episode — skip LLM identification
quotegif find "no soup for you" --episode "Seinfeld S07E06" --format clip

# Longer padding, higher resolution GIF
quotegif find "winter is coming" --pad-before 3 --pad-after 4 --width 640
```

### `quotegif index`

Rebuild the local media library index (cached at `~/.cache/quotegif/index.json`).
Run this after adding new files to your media folders.

### `quotegif config`

Show the resolved configuration and check that ffmpeg is available.

---

## Configuration reference

Full documented example in [`config.example.toml`](config.example.toml).

| Key | Default | Description |
|-----|---------|-------------|
| `media_folders` | `[]` | Directories to scan for video files |
| `output_dir` | `~/quotegifs` | Where to write GIF files |
| `pad_before` | `1.5` | Seconds before quote start to include |
| `pad_after` | `2.5` | Seconds after quote end to include |
| `max_duration` | `12` | Maximum clip length in seconds |
| `gif.fps` | `12` | Frames per second |
| `gif.width` | `480` | Output width in pixels |
| `provider.name` | `"openai"` | Which LLM provider to use |
| `whisper.enabled` | `true` | Use Whisper if subtitles don't match |
| `whisper.model` | `"base"` | Whisper model size (tiny/base/small/medium/large-v3) |

### Environment variables

All settings can be configured via environment variables or a `.env` file. Copy `.env.example` to `.env` to get started.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `TAVILY_API_KEY` | — | Tavily search key (used with Anthropic) |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model override |
| `ANTHROPIC_MODEL` | `claude-3-5-sonnet-20241022` | Anthropic model override |
| `OLLAMA_MODEL` | `llama3.1` | Ollama model override |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama host |
| `QUOTEGIF_PROVIDER` | `openai` | Active LLM provider |
| `QUOTEGIF_MEDIA_FOLDERS` | — | Path-separator-joined list of media dirs |
| `QUOTEGIF_OUTPUT_DIR` | `~/quotegifs` | GIF output directory |
| `QUOTEGIF_PAD_BEFORE` | `1.5` | Seconds before quote |
| `QUOTEGIF_PAD_AFTER` | `2.5` | Seconds after quote |
| `QUOTEGIF_MAX_DURATION` | `12` | Max clip length in seconds |
| `QUOTEGIF_GIF_FPS` | `12` | GIF frames per second |
| `QUOTEGIF_GIF_WIDTH` | `480` | GIF pixel width |
| `QUOTEGIF_WHISPER_ENABLED` | `true` | Enable Whisper fallback |
| `QUOTEGIF_WHISPER_MODEL` | `base` | Whisper model size |
| `QUOTEGIF_WHISPER_DEVICE` | `auto` | Whisper compute device |
| `QUOTEGIF_CONFIG` | — | Path to TOML config file |
| `QUOTEGIF_DOTENV` | — | Path to `.env` file (overrides default search) |

---

## How it works

```
Your vague quote
    ↓
LLM + web search  →  { title, season, episode, exact_quote, confidence }
    ↓
Library index     →  local video file path
    ↓
Subtitles (.srt / embedded)  →  best matching timestamp
    ↓ (if no subtitle match)
Whisper transcription        →  timestamp
    ↓
--format clip  →  ffmpeg stream copy  →  video clip (audio intact)
--format gif   →  burn subs into frames  →  animated GIF
```

### Providers

| Provider | Web search | Notes |
|----------|-----------|-------|
| `openai` | Built-in (Responses API) | Most accurate; recommended |
| `anthropic` | Via Tavily (requires `TAVILY_API_KEY`) | Good accuracy |
| `ollama` | None | Local/private; accuracy depends on model knowledge |

### Subtitle strategy

1. Look for a sidecar `.srt` / `.ass` / `.vtt` file next to the video.
2. Extract embedded subtitle streams via `ffprobe` + `ffmpeg`.
3. If no subtitles found, or no cue scores above the match threshold, fall back to Whisper transcription (if enabled).

---

## Tips

- If the GIF misses the line, try adjusting `--pad-before` / `--pad-after`.
- For long movies with no subtitles, Whisper `large-v3` gives the best accuracy but is slow without a GPU.
- Use `--episode` to skip the LLM step entirely when you already know the source.
- Re-run `quotegif index` whenever you add new media.
