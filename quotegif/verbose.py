from __future__ import annotations

from contextvars import ContextVar

from rich.console import Console

_verbose: ContextVar[bool] = ContextVar("quotegif_verbose", default=False)
_console = Console()


def set_verbose(enabled: bool) -> None:
    _verbose.set(enabled)


def is_verbose() -> bool:
    return _verbose.get()


def section(title: str) -> None:
    if is_verbose():
        _console.print(f"\n[bold magenta]▸ {title}[/bold magenta]")


def log(message: str) -> None:
    if is_verbose():
        _console.print(f"  [dim]{message}[/dim]")


def log_table(table) -> None:
    if is_verbose():
        _console.print(table)
