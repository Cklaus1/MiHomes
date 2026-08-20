"""Configuration CLI commands."""


import typer
from rich.table import Table

from mihomes.cli.formatters import console, esc, format_success
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
    """Get a configuration value. Secrets are masked."""
    with get_session() as session:
        value = config_svc.get_config(session, key)
        if value is not None:
            # SPEC-003 Step 15 — masked on read, here as well as in the web UI. This command
            # printed API keys in full, which is how they end up in terminal scrollback,
            # screenshots and pasted bug reports.
            console.print(f"{esc(key)} = {esc(config_svc.mask_value(key, value))}")
        else:
            console.print(f"[dim]{esc(key)} is not set[/dim]")


@app.command("list")
def list_config():
    """List all configuration values. Secrets are masked."""
    with get_session() as session:
        # `list_config_for_display`, not `list_config`: the unmasked variant still exists for the
        # app paths that need real values, and calling the wrong one here is exactly the mistake
        # this command used to make.
        configs = config_svc.list_config_for_display(session)
        table = Table(title="Configuration")
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_column("Source", style="dim")
        for c in configs:
            table.add_row(esc(c["key"]), esc(c["value"]) or "-", c["source"])
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
