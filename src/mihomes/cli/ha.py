"""CLI for Home Assistant bridge — `mihomes ha <action>`."""

from __future__ import annotations

import asyncio

import typer
from rich import print as rprint
from rich.table import Table

from mihomes.cli.formatters import console

app = typer.Typer(help="Home Assistant integration — setup, test, and run the sensor bridge")


@app.command("setup")
def setup(
    url: str = typer.Option(..., "--url", "-u", help="HA base URL (e.g. http://homeassistant.local:8123)"),
    token: str = typer.Option(..., "--token", "-t", help="Long-lived access token from HA profile"),
    default_property: str = typer.Option(
        None, "--property", "-p", help="Default property slug for sensor → issue mapping"
    ),
):
    """Configure Home Assistant connection."""
    from mihomes.db import get_session
    from mihomes.ha.config import save_ha_config

    with get_session() as session:
        save_ha_config(
            session,
            url=url,
            token=token,
            default_property=default_property,
        )
        session.commit()

    rprint("[green]✓[/green] HA configuration saved.")
    if default_property:
        rprint(f"  Default property: [bold]{default_property}[/bold]")
    rprint("\nRun [bold]mihomes ha test[/bold] to verify the connection.")


@app.command("test")
def test():
    """Test the Home Assistant connection."""
    from mihomes.db import get_session
    from mihomes.ha.config import get_ha_ws_url, get_ha_token

    with get_session() as session:
        ws_url = get_ha_ws_url(session)
        token = get_ha_token(session)

    if not ws_url or not token:
        rprint("[red]✗[/red] HA is not configured. Run: [bold]mihomes ha setup[/bold]")
        raise typer.Exit(1)

    rprint(f"Testing connection to [bold]{ws_url}[/bold]…")

    from mihomes.ha.client import test_connection
    result = asyncio.run(test_connection(ws_url, token))

    if result["ok"]:
        rprint(f"[green]✓[/green] Connected! Home Assistant version: [bold]{result['ha_version']}[/bold]")
    else:
        rprint(f"[red]✗[/red] Connection failed: {result['error']}")
        raise typer.Exit(1)


@app.command("status")
def status():
    """Show HA bridge configuration."""
    from mihomes.db import get_session
    from mihomes.ha.config import get_ha_url, get_ha_token, get_default_property, is_ha_enabled

    with get_session() as session:
        url = get_ha_url(session)
        token = get_ha_token(session)
        default_prop = get_default_property(session)
        enabled = is_ha_enabled(session)

    table = Table(title="Home Assistant Bridge Status", show_header=False)
    table.add_column("Key", style="bold")
    table.add_column("Value")

    configured = bool(url and token)
    table.add_row("Configured", "[green]Yes[/green]" if configured else "[red]No[/red]")
    table.add_row("Enabled", "[green]Yes[/green]" if enabled else "[yellow]No[/yellow]")
    table.add_row("HA URL", url or "[dim]not set[/dim]")
    table.add_row("Access Token", ("*" * 8 + token[-4:]) if token else "[dim]not set[/dim]")
    table.add_row("Default Property", default_prop or "[dim]not set[/dim]")

    console.print(table)

    if not configured:
        rprint("\nRun [bold]mihomes ha setup --url <url> --token <token>[/bold] to configure.")


@app.command("run")
def run(
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Logging level"),
):
    """Run the HA bridge (blocks, subscribes to sensor events)."""
    from mihomes.db import get_session
    from mihomes.ha.config import get_ha_ws_url, get_ha_token

    with get_session() as session:
        ws_url = get_ha_ws_url(session)
        token = get_ha_token(session)

    if not ws_url or not token:
        rprint("[red]✗[/red] HA is not configured. Run: [bold]mihomes ha setup[/bold]")
        raise typer.Exit(1)

    rprint(f"[bold]Starting HA bridge[/bold] → {ws_url}")
    rprint("[dim]Press Ctrl+C to stop.[/dim]\n")

    from mihomes.ha.bridge import run_bridge
    try:
        asyncio.run(run_bridge(log_level=log_level))
    except KeyboardInterrupt:
        rprint("\n[yellow]Bridge stopped.[/yellow]")


@app.command("rules")
def rules():
    """List all sensor detection rules."""
    from mihomes.ha.rules import RULES

    table = Table(title="HA Sensor Rules", show_header=True, header_style="bold")
    table.add_column("Pattern", style="code")
    table.add_column("Trigger")
    table.add_column("Severity")
    table.add_column("Action")

    for rule in RULES:
        trigger = (
            rule.state_trigger
            if isinstance(rule.state_trigger, (str, list))
            else "<function>"
        )
        if isinstance(trigger, list):
            trigger = ", ".join(trigger)
        table.add_row(
            rule.pattern,
            str(trigger),
            rule.severity.value,
            "Alert" if rule.alert_only else "Issue",
        )

    console.print(table)
