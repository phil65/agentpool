#!/usr/bin/env python3
"""Database migration management script for agentpool.

This script provides easy commands for managing database migrations using Alembic.
"""

import os
from pathlib import Path
import subprocess
from typing import Annotated

from rich.console import Console
from rich.prompt import Confirm
import typer


app = typer.Typer(
    name="agentpool-db",
    help="Database migration management for agentpool",
    rich_markup_mode="rich",
)
console = Console()


def run_command(cmd: list[str], cwd: Path | None = None) -> int:
    """Run a command and return the exit code."""
    console.print(f"[bold blue]Running:[/bold blue] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0 and cmd[0] == "alembic":
        console.print(
            f"[bold red]Error:[/bold red] Command {cmd[0]!r} not found. "
            "Make sure alembic is installed."
        )
    return result.returncode


def get_project_root() -> Path:
    """Get the project root directory."""
    # Start from this file and go up to find the project root
    # This file is in src/agentpool_storage/sql_provider/cli.py
    return Path(__file__).parent.parent.parent.parent


@app.command()
def status() -> int:
    """Show current migration status."""
    project_root = get_project_root()
    return run_command(["alembic", "current", "-v"], cwd=project_root)


@app.command()
def upgrade(
    revision: Annotated[str, typer.Argument(help="Target revision")] = "head",
) -> int:
    """Upgrade database to latest migration."""
    project_root = get_project_root()
    return run_command(["alembic", "upgrade", revision], cwd=project_root)


@app.command()
def downgrade(
    revision: Annotated[
        str,
        typer.Argument(help="Target revision (e.g., -1 for previous, or specific revision ID)"),
    ],
) -> int:
    """Downgrade database."""
    project_root = get_project_root()
    return run_command(["alembic", "downgrade", revision], cwd=project_root)


@app.command()
def create(
    message: Annotated[str, typer.Argument(help="Migration message")],
    autogenerate: Annotated[
        bool,
        typer.Option(
            "--autogenerate",
            help="Auto-generate migration based on model changes",
        ),
    ] = False,
) -> int:
    """Create a new migration."""
    project_root = get_project_root()
    cmd = ["alembic", "revision", "-m", message]
    if autogenerate:
        cmd.append("--autogenerate")
    return run_command(cmd, cwd=project_root)


@app.command()
def history() -> int:
    """Show migration history."""
    project_root = get_project_root()
    return run_command(["alembic", "history", "-v"], cwd=project_root)


@app.command()
def current() -> int:
    """Show current revision."""
    project_root = get_project_root()
    return run_command(["alembic", "current"], cwd=project_root)


@app.command()
def reset(
    force: Annotated[
        bool,
        typer.Option("--force", help="Force reset without confirmation"),
    ] = False,
) -> int:
    """Reset database (WARNING: destructive)."""
    if not force and not Confirm.ask(
        "[bold red]This will delete all data in the database. Continue?[/bold red]",
        default=False,
    ):
        console.print("[yellow]Aborted.[/yellow]")
        return 0

    project_root = get_project_root()
    console.print("[bold yellow]Resetting database...[/bold yellow]")

    # First, drop to base (empty database)
    result = run_command(["alembic", "downgrade", "base"], cwd=project_root)
    if result != 0:
        return result

    # Then upgrade to head
    return run_command(["alembic", "upgrade", "head"], cwd=project_root)


def main() -> None:
    """Main entry point."""
    from dotenv import load_dotenv

    load_dotenv()
    # Set up environment
    project_root = get_project_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    os.environ.update(env)

    app()


if __name__ == "__main__":
    main()
