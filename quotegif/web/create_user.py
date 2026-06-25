"""Create a QuoteGif web UI user."""

from __future__ import annotations

import getpass
import sys

import typer

from quotegif.web.db import create_user, init_db

app = typer.Typer(help="Manage QuoteGif web UI users.")


@app.command("create-user")
def create_user_cmd(
    username: str = typer.Argument(help="Login username"),
    password: str | None = typer.Argument(
        None,
        help="Password (min 8 chars). Prompted securely if omitted.",
    ),
    password_flag: str | None = typer.Option(
        None,
        "--password",
        help="Password (alternative to positional arg; useful in scripts)",
    ),
) -> None:
    """Add a username/password for the web UI."""
    init_db()
    resolved = password_flag or password
    if resolved is None:
        resolved = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if resolved != confirm:
            typer.echo("Passwords do not match.", err=True)
            raise typer.Exit(1)
    try:
        create_user(username, resolved)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Created user {username!r}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
