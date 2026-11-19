"""Backup and doctor CLI commands."""

from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.services import backup as backup_svc

app = typer.Typer(name="backup", help="Backup and restore", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def create_backup(
    ctx: typer.Context,
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path"),
):
    """Create a backup of database and media."""
    if ctx.invoked_subcommand is not None:
        return
    path = backup_svc.create_backup(Path(output) if output else None)
    format_success(f"Backup created: {path}")


@app.command("list")
def list_backups():
    """List available backups."""
    backups = backup_svc.list_backups()
    if not backups:
        console.print("[dim]No backups found.[/dim]")
        return
    table = Table(title="Backups")
    table.add_column("Name", style="bold")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Created")
    for b in backups:
        table.add_row(b["name"], str(b["size_mb"]), b["modified"])
    console.print(table)


@app.command("restore")
def restore_backup(
    backup_file: str = typer.Argument(..., help="Path to backup file"),
    force: bool = typer.Option(
        False, "--force", help="Restore even if background services are running (unsafe)"
    ),
):
    """Restore from a backup."""
    path = Path(backup_file)
    if not path.exists():
        format_error(f"File not found: {backup_file}")
        raise typer.Exit(1)
    typer.confirm(f"Restore from {path.name}? This will overwrite current data.", abort=True)
    try:
        count = backup_svc.restore_backup(path, force=force)
    except RuntimeError as e:
        format_error(str(e))
        raise typer.Exit(1) from e
    format_success(f"Restored {count} object(s) from {path.name}")
