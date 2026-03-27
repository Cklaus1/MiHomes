"""Configuration CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.services import config_service as config_svc

app = typer.Typer(name="config", help="Manage MiHomes configuration")


@app.command("set")
def set_config(
    key: str = typer.Argument(..., help="Configuration key"),
    value: str = typer.Argument(..., help="Configuration value"),
):
    """Set a configuration value."""
    with get_session() as session:
        config_svc.set_config(session, key, value)
        format_success(f"{key} = {value}")


@app.command("get")
def get_config(
    key: str = typer.Argument(..., help="Configuration key"),
):
    """Get a configuration value."""
    with get_session() as session:
        value = config_svc.get_config(session, key)
        if value is not None:
            console.print(f"{key} = {value}")
        else:
            console.print(f"[dim]{key} is not set[/dim]")


@app.command("list")
def list_config():
    """List all configuration values."""
    with get_session() as session:
        configs = config_svc.list_config(session)
        table = Table(title="Configuration")
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_column("Source", style="dim")
        for c in configs:
            table.add_row(c["key"], c["value"] or "-", c["source"])
        console.print(table)


@app.command("reset")
def reset_config(
    key: str = typer.Argument(..., help="Configuration key to reset to default"),
):
    """Reset a configuration value to its default."""
    with get_session() as session:
        config_svc.reset_config(session, key)
        default = config_svc.get_config(session, key)
        format_success(f"{key} reset to default ({default})")
