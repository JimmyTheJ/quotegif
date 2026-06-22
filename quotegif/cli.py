from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from quotegif.config import AppConfig, check_ffmpeg, load_config
from quotegif.models import ClipSpec, EpisodeRef

app = typer.Typer(
    name="quotegif",
    help="Turn a vague remembered quote into an animated GIF of the exact moment.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


def _load_cfg(config_path: Path | None) -> AppConfig:
    try:
        return load_config(config_path)
    except Exception as e:
        err_console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)


def _require_ffmpeg() -> None:
    ok, msg = check_ffmpeg()
    if not ok:
        err_console.print(f"[red]{msg}[/red]")
        raise typer.Exit(1)


@app.command()
def find(
    quote: Annotated[str, typer.Argument(help="The quote to search for (vague is fine)")],
    pad_before: Annotated[float, typer.Option("--pad-before", help="Seconds before quote start")] = -1,
    pad_after: Annotated[float, typer.Option("--pad-after", help="Seconds after quote end")] = -1,
    fps: Annotated[int, typer.Option("--fps", help="GIF frames per second")] = -1,
    width: Annotated[int, typer.Option("--width", help="GIF pixel width")] = -1,
    provider: Annotated[Optional[str], typer.Option("--provider", help="Override LLM provider")] = None,
    episode: Annotated[Optional[str], typer.Option("--episode", help='Skip LLM, specify episode directly e.g. "The Office S03E14"')] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-confirm low-confidence and ambiguous matches")] = False,
    open_gif: Annotated[bool, typer.Option("--open", help="Open the GIF after creation")] = False,
    config_path: Annotated[Optional[Path], typer.Option("--config", help="Path to config TOML")] = None,
) -> None:
    """Find a quote in your media library and render it as an animated GIF."""
    _require_ffmpeg()
    cfg = _load_cfg(config_path)

    # Apply CLI overrides
    if pad_before >= 0:
        cfg.pad_before = pad_before
    if pad_after >= 0:
        cfg.pad_after = pad_after
    if fps > 0:
        cfg.gif.fps = fps
    if width > 0:
        cfg.gif.width = width

    if not cfg.media_folders:
        err_console.print(
            "[red]No media_folders configured.[/red] "
            "Add them to your config file (run [bold]quotegif config[/bold] to see where)."
        )
        raise typer.Exit(1)

    # Step 1: Identify the source
    ref: EpisodeRef
    if episode:
        ref = _parse_episode_string(episode, quote)
        console.print(f"[dim]Using provided episode:[/dim] {ref.display()}")
    else:
        console.print(f"[bold cyan]Identifying:[/bold cyan] \"{quote}\"")
        try:
            from quotegif.identify import identify_quote
            with console.status("Asking LLM (with web search)…"):
                ref = identify_quote(quote, cfg, provider_override=provider)
        except Exception as e:
            err_console.print(f"[red]Identification failed:[/red] {e}")
            raise typer.Exit(1)

        _show_ref(ref)

        if ref.confidence < 0.6:
            console.print(
                f"[yellow]Low confidence ({ref.confidence:.0%}):[/yellow] {ref.reasoning}"
            )
            if not yes and not Confirm.ask("Proceed anyway?", default=False):
                raise typer.Exit(0)

    # Step 2: Find the local file
    console.print(f"[bold cyan]Searching library[/bold cyan] for: {ref.display()}")
    from quotegif.library import find_media, get_index

    with console.status("Loading library index…"):
        entries = get_index(cfg)

    matches = find_media(ref, entries)
    if not matches:
        err_console.print(
            f"[red]No matching file found[/red] for [bold]{ref.display()}[/bold]. "
            "Run [bold]quotegif index[/bold] to rebuild the index."
        )
        raise typer.Exit(1)

    media_path = matches[0].path
    if len(matches) > 1 and not yes:
        media_path = _pick_file(matches)

    console.print(f"[dim]File:[/dim] {media_path}")

    # Step 3: Load subtitles, fall back to Whisper
    search_query = ref.exact_quote or quote
    from quotegif.matcher import match_quote
    from quotegif.subtitles import get_cues

    with console.status("Loading subtitles…"):
        cues = get_cues(media_path)

    best_cue = None
    if cues:
        console.print(f"[dim]Found {len(cues)} subtitle cues.[/dim]")
        best_cue = match_quote(search_query, cues)

    if best_cue is None and cfg.whisper.enabled:
        console.print("[yellow]No subtitle match found.[/yellow] Falling back to Whisper transcription…")
        try:
            from quotegif.transcribe import transcribe
            with console.status(f"Transcribing with Whisper ({cfg.whisper.model})… this may take a while"):
                whisper_cues = transcribe(media_path, cfg.whisper.model, cfg.whisper.device)
            best_cue = match_quote(search_query, whisper_cues)
        except ImportError as e:
            err_console.print(f"[red]Whisper not available:[/red] {e}")
        except Exception as e:
            err_console.print(f"[red]Transcription failed:[/red] {e}")

    if best_cue is None:
        err_console.print(
            "[red]Could not locate the quote in the episode.[/red] "
            "Try a more specific quote or check the episode identifier."
        )
        raise typer.Exit(1)

    console.print(
        f"[green]Matched cue[/green] at "
        f"[bold]{best_cue.start:.1f}s – {best_cue.end:.1f}s[/bold]: "
        f"[italic]\"{best_cue.text}\"[/italic]"
    )

    # Step 4: Render GIF
    spec = ClipSpec(
        media_path=media_path,
        cue=best_cue,
        pad_before=cfg.pad_before,
        pad_after=cfg.pad_after,
        max_duration=cfg.max_duration,
    )
    console.print(
        f"[bold cyan]Rendering GIF[/bold cyan] "
        f"({spec.clip_start:.1f}s – {spec.clip_end:.1f}s, "
        f"{spec.duration:.1f}s, {cfg.gif.fps}fps, {cfg.gif.width}px wide)…"
    )

    from quotegif.gifmaker import make_gif
    try:
        with console.status("Running ffmpeg…"):
            out_path = make_gif(
                spec=spec,
                output_dir=cfg.output_dir,
                fps=cfg.gif.fps,
                width=cfg.gif.width,
                episode_label=ref.display(),
            )
    except RuntimeError as e:
        err_console.print(f"[red]GIF creation failed:[/red] {e}")
        raise typer.Exit(1)

    console.print(Panel(f"[bold green]Done![/bold green] {out_path}", expand=False))

    if open_gif:
        _open_file(out_path)


@app.command()
def index(
    config_path: Annotated[Optional[Path], typer.Option("--config", help="Path to config TOML")] = None,
) -> None:
    """Rebuild the local media library index."""
    cfg = _load_cfg(config_path)

    if not cfg.media_folders:
        err_console.print("[red]No media_folders configured.[/red]")
        raise typer.Exit(1)

    console.print("[bold cyan]Indexing media folders…[/bold cyan]")
    from quotegif.library import build_index, save_index

    with console.status("Scanning…"):
        entries = build_index(cfg.media_folders, verbose=True)
    save_index(entries)

    table = Table(title=f"Indexed {len(entries)} files")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("S")
    table.add_column("E")
    table.add_column("Path", overflow="fold")

    for e in entries[:30]:
        table.add_row(
            e.title,
            e.media_type,
            str(e.season) if e.season else "–",
            str(e.episode) if e.episode else "–",
            str(e.path),
        )
    if len(entries) > 30:
        table.add_row("…", "", "", "", f"(and {len(entries) - 30} more)")

    console.print(table)
    console.print(f"[green]Index saved.[/green] Total: {len(entries)} entries.")


@app.command(name="config")
def show_config(
    config_path: Annotated[Optional[Path], typer.Option("--config", help="Path to config TOML")] = None,
) -> None:
    """Show resolved configuration and check ffmpeg."""
    cfg = _load_cfg(config_path)
    ok, ffmpeg_msg = check_ffmpeg()

    table = Table(title="Resolved Configuration", show_header=False)
    table.add_column("Setting", style="bold")
    table.add_column("Value")

    table.add_row("media_folders", "\n".join(str(f) for f in cfg.media_folders) or "(none)")
    table.add_row("output_dir", str(cfg.output_dir))
    table.add_row("pad_before", f"{cfg.pad_before}s")
    table.add_row("pad_after", f"{cfg.pad_after}s")
    table.add_row("max_duration", f"{cfg.max_duration}s")
    table.add_row("gif.fps", str(cfg.gif.fps))
    table.add_row("gif.width", f"{cfg.gif.width}px")
    table.add_row("provider", cfg.provider.name)
    table.add_row("whisper.enabled", str(cfg.whisper.enabled))
    table.add_row("whisper.model", cfg.whisper.model)
    table.add_row("ffmpeg", f"{'[green]OK[/green]' if ok else '[red]MISSING[/red]'} – {ffmpeg_msg}")

    console.print(table)

    # Config file location info
    from quotegif.config import _DEFAULT_CONFIG_PATHS
    console.print("\n[dim]Default config search paths:[/dim]")
    for p in _DEFAULT_CONFIG_PATHS:
        exists = "[green]exists[/green]" if p.exists() else "[dim]not found[/dim]"
        console.print(f"  {p}  {exists}")
    console.print("  (or set [bold]QUOTEGIF_CONFIG[/bold] env var)")


# ---- helpers ----

def _show_ref(ref: EpisodeRef) -> None:
    table = Table(title="Identified Source", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Title", ref.title)
    table.add_row("Type", ref.media_type)
    if ref.season:
        table.add_row("Season", str(ref.season))
    if ref.episode:
        table.add_row("Episode", str(ref.episode))
    if ref.episode_title:
        table.add_row("Episode title", ref.episode_title)
    if ref.exact_quote:
        table.add_row("Exact quote", f'"{ref.exact_quote}"')
    table.add_row("Confidence", f"{ref.confidence:.0%}")
    table.add_row("Reasoning", ref.reasoning)
    console.print(table)


def _pick_file(matches) -> Path:
    console.print("\n[yellow]Multiple matching files found:[/yellow]")
    for i, m in enumerate(matches[:10], 1):
        console.print(f"  [bold]{i}[/bold]. {m.path}")
    choice = Prompt.ask(
        "Pick one",
        choices=[str(i) for i in range(1, min(len(matches), 10) + 1)],
        default="1",
    )
    return matches[int(choice) - 1].path


def _parse_episode_string(text: str, fallback_quote: str) -> EpisodeRef:
    """Parse a string like 'The Office S03E14' into an EpisodeRef."""
    import re
    pattern = r"^(.*?)\s+[Ss](\d+)[Ee](\d+)\s*$"
    m = re.match(pattern, text.strip())
    if m:
        return EpisodeRef(
            title=m.group(1).strip(),
            media_type="tv",
            season=int(m.group(2)),
            episode=int(m.group(3)),
            exact_quote=fallback_quote,
            confidence=1.0,
        )
    return EpisodeRef(
        title=text.strip(),
        media_type="movie",
        exact_quote=fallback_quote,
        confidence=1.0,
    )


def _open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception:
        pass
