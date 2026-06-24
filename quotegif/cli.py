from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from quotegif.config import AppConfig, check_ffmpeg, load_config
from quotegif.models import EpisodeRef
from quotegif.pipeline import OutputFormat, locate_quote, render_output

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
    provider: Annotated[Optional[str], typer.Option("--provider", help="LLM provider: openai | anthropic | ollama")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="Override the model used by the provider (e.g. gpt-4o-mini, llama3.2)")] = None,
    episode: Annotated[Optional[str], typer.Option("--episode", help='Skip LLM, specify episode directly e.g. "The Office S03E14"')] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-confirm low-confidence and ambiguous matches")] = False,
    output_format: Annotated[OutputFormat, typer.Option(
        "--format",
        help="clip: video+audio (same codec when possible) | gif: silent GIF with burned-in subtitles",
    )] = "gif",
    open_output: Annotated[bool, typer.Option("--open", help="Open the output file after creation")] = False,
    config_path: Annotated[Optional[Path], typer.Option("--config", help="Path to config TOML")] = None,
) -> None:
    """Find a quote in your media library and render a clip or subtitled GIF."""
    _require_ffmpeg()
    cfg = _load_cfg(config_path)

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
        provider_name = provider or cfg.provider.name
        from quotegif.providers.registry import get_active_model
        model_label = get_active_model(cfg, provider_name, model)
        console.print(
            f"[bold cyan]Identifying:[/bold cyan] \"{quote}\"  "
            f"[dim]({provider_name} / {model_label})[/dim]"
        )
        try:
            from quotegif.identify import identify_quote
            with console.status("Asking LLM (with web search)…"):
                ref = identify_quote(quote, cfg, provider_override=provider, model_override=model)
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

    try:
        with console.status("Locating quote in file…"):
            locate = locate_quote(ref, quote, cfg, media_path)
    except LookupError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except ImportError as e:
        err_console.print(f"[red]Whisper not available:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        err_console.print(f"[red]Location failed:[/red] {e}")
        raise typer.Exit(1)

    _print_matched_cue(locate)

    try:
        with console.status("Running ffmpeg…"):
            out_path = _render_and_report(locate, cfg, ref.display(), output_format)
    except RuntimeError as e:
        err_console.print(f"[red]Render failed:[/red] {e}")
        raise typer.Exit(1)

    console.print(Panel(f"[bold green]Done![/bold green] {out_path}", expand=False))

    if open_output:
        _open_file(out_path)


@app.command()
def compare(
    quote: Annotated[str, typer.Argument(help="The quote to identify")],
    providers: Annotated[Optional[str], typer.Option(
        "--providers",
        help="Comma-separated providers to compare (default: all configured). e.g. openai,ollama",
    )] = None,
    models: Annotated[Optional[str], typer.Option(
        "--models",
        help="Comma-separated model overrides, matched by position to --providers. e.g. gpt-4o-mini,llama3.2",
    )] = None,
    gif: Annotated[bool, typer.Option("--gif", help="After comparing, pick a result and render output")] = False,
    output_format: Annotated[OutputFormat, typer.Option(
        "--format",
        help="clip: video+audio | gif: silent GIF with burned-in subtitles (used with --gif)",
    )] = "gif",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-confirm when proceeding to GIF")] = False,
    config_path: Annotated[Optional[Path], typer.Option("--config", help="Path to config TOML")] = None,
) -> None:
    """
    Run quote identification through multiple providers in parallel and compare results.

    Examples:

      quotegif compare "that's what she said"
      quotegif compare "no soup for you" --providers openai,ollama
      quotegif compare "no soup for you" --providers openai,ollama --models gpt-4o-mini,llama3.2
      quotegif compare "no soup for you" --gif
    """
    cfg = _load_cfg(config_path)

    from quotegif.identify import identify_quote
    from quotegif.providers.registry import KNOWN_PROVIDERS, get_active_model

    # Resolve which providers to run
    if providers:
        provider_list = [p.strip() for p in providers.split(",")]
        unknown = [p for p in provider_list if p not in KNOWN_PROVIDERS]
        if unknown:
            err_console.print(f"[red]Unknown providers:[/red] {', '.join(unknown)}. Choose from: {', '.join(KNOWN_PROVIDERS)}")
            raise typer.Exit(1)
    else:
        # Default: run all providers that appear to have credentials
        provider_list = list(_configured_providers(cfg))
        if not provider_list:
            err_console.print("[red]No providers appear to be configured.[/red] Set API keys or run with --providers.")
            raise typer.Exit(1)

    # Resolve per-provider model overrides
    model_list: list[str | None]
    if models:
        raw = [m.strip() or None for m in models.split(",")]
        # Pad or truncate to match provider_list length
        model_list = (raw + [None] * len(provider_list))[: len(provider_list)]
    else:
        model_list = [None] * len(provider_list)

    provider_model_pairs = list(zip(provider_list, model_list))

    console.print(f"[bold cyan]Comparing[/bold cyan] \"{quote}\" across {len(provider_model_pairs)} provider(s)…\n")

    # Run all providers in parallel
    results: dict[str, EpisodeRef | Exception] = {}

    def _run(pname: str, moverride: str | None) -> tuple[str, EpisodeRef | Exception]:
        try:
            ref = identify_quote(quote, cfg, provider_override=pname, model_override=moverride)
            return pname, ref
        except Exception as exc:
            return pname, exc

    with ThreadPoolExecutor(max_workers=len(provider_model_pairs)) as pool:
        futures = {
            pool.submit(_run, pname, moverride): (pname, moverride)
            for pname, moverride in provider_model_pairs
        }
        # Show a live status while waiting
        completed = 0
        total = len(futures)
        with console.status(f"Waiting for results (0/{total})…") as status:
            for future in as_completed(futures):
                pname, ref_or_exc = future.result()
                results[pname] = ref_or_exc
                completed += 1
                status.update(f"Waiting for results ({completed}/{total})…")

    # Display comparison table
    table = Table(title=f'Comparison results for: "{quote}"', show_lines=True)
    table.add_column("Provider", style="bold", no_wrap=True)
    table.add_column("Model", style="dim", no_wrap=True)
    table.add_column("Title")
    table.add_column("Episode")
    table.add_column("Exact quote", overflow="fold")
    table.add_column("Conf", justify="right")
    table.add_column("Reasoning", overflow="fold")

    ordered_results: list[tuple[str, str | None, EpisodeRef | Exception]] = []
    for pname, moverride in provider_model_pairs:
        res = results.get(pname)
        model_label = get_active_model(cfg, pname, moverride)
        ordered_results.append((pname, model_label, res))

        if isinstance(res, Exception):
            table.add_row(pname, model_label, f"[red]ERROR[/red]", "", str(res), "", "")
        else:
            conf_color = "green" if res.confidence >= 0.7 else "yellow" if res.confidence >= 0.4 else "red"
            table.add_row(
                pname,
                model_label,
                res.title,
                res.display() if res.media_type == "tv" else (str(res.year) if res.year else "–"),
                f'"{res.exact_quote}"' if res.exact_quote else "–",
                f"[{conf_color}]{res.confidence:.0%}[/{conf_color}]",
                res.reasoning,
            )

    console.print(table)

    if not gif:
        # Print hint for continuing to GIF
        console.print(
            "\n[dim]To render from one of these results, run:[/dim]\n"
            f'  quotegif find "{quote}" --provider <name>\n'
            f'  quotegif find "{quote}" --format clip\n'
            "Or re-run with [bold]--gif[/bold] to pick interactively."
        )
        return

    # -- Optional: pick a result and continue to GIF --
    _require_ffmpeg()

    if not cfg.media_folders:
        err_console.print("[red]No media_folders configured.[/red]")
        raise typer.Exit(1)

    successful = [(pname, mlabel, res) for pname, mlabel, res in ordered_results if isinstance(res, EpisodeRef)]
    if not successful:
        err_console.print("[red]All providers failed — cannot proceed to GIF.[/red]")
        raise typer.Exit(1)

    if len(successful) == 1 or yes:
        chosen_name, chosen_model, chosen_ref = successful[0]
    else:
        console.print("\n[bold]Which result do you want to use for the GIF?[/bold]")
        for i, (pname, mlabel, res) in enumerate(successful, 1):
            console.print(f"  [bold]{i}[/bold]. {pname} / {mlabel}  →  {res.display()}  ({res.confidence:.0%})")
        choice = Prompt.ask(
            "Pick one",
            choices=[str(i) for i in range(1, len(successful) + 1)],
            default="1",
        )
        chosen_name, chosen_model, chosen_ref = successful[int(choice) - 1]

    console.print(f"\n[dim]Using:[/dim] {chosen_name} / {chosen_model}  →  {chosen_ref.display()}")
    _render_from_ref(chosen_ref, quote, cfg, yes=yes, output_format=output_format)


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


@app.command(name="whisper-check")
def whisper_check(
    config_path: Annotated[Optional[Path], typer.Option("--config", help="Path to config TOML")] = None,
) -> None:
    """Verify faster-whisper can use the configured device (CPU or CUDA)."""
    cfg = _load_cfg(config_path)

    table = Table(title="Whisper check", show_header=False)
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_row("enabled", str(cfg.whisper.enabled))
    table.add_row("model", cfg.whisper.model)
    table.add_row("device (config)", cfg.whisper.device)

    try:
        import ctranslate2
        cuda_count = ctranslate2.get_cuda_device_count()
        table.add_row("ctranslate2 CUDA devices", str(cuda_count))
    except Exception as e:
        table.add_row("ctranslate2", f"[red]error: {e}[/red]")
        console.print(table)
        raise typer.Exit(1)

    device = cfg.whisper.device
    if device == "auto":
        try:
            import torch  # type: ignore[import]
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cuda" if cuda_count > 0 else "cpu"
    table.add_row("resolved device", device)

    try:
        from faster_whisper import WhisperModel
        import subprocess
        import tempfile

        compute = "float16" if device == "cuda" else "int8"
        with console.status(f"Loading Whisper ({cfg.whisper.model} on {device})…"):
            model = WhisperModel(cfg.whisper.model, device=device, compute_type=compute)
        table.add_row("model load", "[green]OK[/green]")

        # Model load alone is not enough — run a short inference to verify libcublas.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "anullsrc=duration=0.5",
                    "-ar", "16000", "-ac", "1", wav_path,
                ],
                check=True,
                capture_output=True,
            )
            with console.status("Running inference probe…"):
                list(model.transcribe(wav_path, beam_size=1))
            table.add_row("inference probe", "[green]OK[/green]")
        finally:
            Path(wav_path).unlink(missing_ok=True)
    except Exception as e:
        table.add_row("model load", f"[red]FAILED: {e}[/red]")
        if "libcublas" in str(e).lower():
            err_console.print(
                "\n[yellow]Hint:[/yellow] CUDA libraries missing in this environment. "
                "Docker GPU users should rebuild with [bold]Dockerfile.gpu[/bold] "
                "and run via [bold]./qg-gpu[/bold]."
            )
        console.print(table)
        raise typer.Exit(1)

    console.print(table)


@app.command(name="config")
def show_config(
    config_path: Annotated[Optional[Path], typer.Option("--config", help="Path to config TOML")] = None,
) -> None:
    """Show resolved configuration and check ffmpeg."""
    cfg = _load_cfg(config_path)
    ok, ffmpeg_msg = check_ffmpeg()

    from quotegif.providers.registry import KNOWN_PROVIDERS, get_active_model

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
    table.add_row("provider (default)", cfg.provider.name)

    for pname in KNOWN_PROVIDERS:
        model = get_active_model(cfg, pname)
        has_key = _provider_has_key(cfg, pname)
        key_indicator = "[green]key set[/green]" if has_key else "[dim]no key[/dim]"
        table.add_row(f"  {pname}", f"{model}  {key_indicator}")

    table.add_row("whisper.enabled", str(cfg.whisper.enabled))
    table.add_row("whisper.model", cfg.whisper.model)
    table.add_row("ffmpeg", f"{'[green]OK[/green]' if ok else '[red]MISSING[/red]'} – {ffmpeg_msg}")

    console.print(table)

    from quotegif.config import _DEFAULT_CONFIG_PATHS
    console.print("\n[dim]Default config search paths:[/dim]")
    for p in _DEFAULT_CONFIG_PATHS:
        exists = "[green]exists[/green]" if p.exists() else "[dim]not found[/dim]"
        console.print(f"  {p}  {exists}")
    console.print("  (or set [bold]QUOTEGIF_CONFIG[/bold] env var)")


# ---- shared rendering (used by find and compare --gif) ----

def _render_from_ref(
    ref: EpisodeRef,
    original_quote: str,
    cfg: AppConfig,
    yes: bool = False,
    output_format: OutputFormat = "gif",
) -> None:
    from quotegif.library import find_media, get_index

    console.print(f"[bold cyan]Searching library[/bold cyan] for: {ref.display()}")
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

    try:
        with console.status("Locating quote in file…"):
            locate = locate_quote(ref, original_quote, cfg, media_path)
    except LookupError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        err_console.print(f"[red]Location failed:[/red] {e}")
        raise typer.Exit(1)

    _print_matched_cue(locate)

    try:
        with console.status("Running ffmpeg…"):
            out_path = _render_and_report(locate, cfg, ref.display(), output_format)
    except RuntimeError as e:
        err_console.print(f"[red]Render failed:[/red] {e}")
        raise typer.Exit(1)

    console.print(Panel(f"[bold green]Done![/bold green] {out_path}", expand=False))


def _print_matched_cue(locate) -> None:
    cue = locate.matched_cue
    spec = locate.spec
    sub_count = len(locate.subtitle_cues)
    if sub_count:
        console.print(f"[dim]Loaded {sub_count} subtitle cues.[/dim]")
    console.print(
        f"[green]Matched cue[/green] at "
        f"[bold]{cue.start:.1f}s – {cue.end:.1f}s[/bold]: "
        f"[italic]\"{cue.text}\"[/italic]"
    )
    console.print(
        f"[dim]Clip window:[/dim] {spec.clip_start:.1f}s – {spec.clip_end:.1f}s "
        f"({spec.duration:.1f}s)"
    )


def _render_and_report(locate, cfg: AppConfig, episode_label: str, output_format: OutputFormat) -> Path:
    spec = locate.spec
    if output_format == "clip":
        console.print(
            f"[bold cyan]Rendering clip[/bold cyan] "
            f"({spec.clip_start:.1f}s – {spec.clip_end:.1f}s, "
            f"{spec.duration:.1f}s, video+audio)…"
        )
    else:
        console.print(
            f"[bold cyan]Rendering GIF[/bold cyan] "
            f"({spec.clip_start:.1f}s – {spec.clip_end:.1f}s, "
            f"{spec.duration:.1f}s, {cfg.gif.fps}fps, {cfg.gif.width}px, subtitles burned in)…"
        )
    return render_output(locate, cfg, episode_label, output_format)


# ---- helpers ----

def _configured_providers(cfg: AppConfig) -> list[str]:
    """Return provider names that have credentials or don't require them (ollama)."""
    out = []
    if cfg.provider.openai.api_key:
        out.append("openai")
    if cfg.provider.anthropic.api_key:
        out.append("anthropic")
    # Ollama needs no API key — include it if it's the default or if openai is also present
    out.append("ollama")
    return out


def _provider_has_key(cfg: AppConfig, name: str) -> bool:
    if name == "openai":
        return bool(cfg.provider.openai.api_key)
    if name == "anthropic":
        return bool(cfg.provider.anthropic.api_key)
    if name == "ollama":
        return True  # local, no key needed
    return False


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
