"""Alerts CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success, severity_color
from mihomes.db import get_session
from mihomes.services import alerts as alert_svc

app = typer.Typer(name="alerts", help="View and manage alerts", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def show_alerts(
    ctx: typer.Context,
    format: str = typer.Option("rich", "--format", help="Output format: rich, brief, json"),
):
    """Show all pending alerts."""
    if ctx.invoked_subcommand is not None:
        return

    with get_session() as session:
        alert_svc.generate_alerts(session)
        alerts = alert_svc.list_alerts(session)

        if not alerts:
            console.print("[green]No pending alerts.[/green]")
            return

        if format == "brief":
            for a in alerts:
                print(f"[{a.severity.value}] {a.message}")
            return

        if format == "json":
            import json
            data = [{"id": a.id, "type": a.alert_type, "severity": a.severity.value, "message": a.message} for a in alerts]
            print(json.dumps(data, indent=2))
            return

        table = Table(title=f"Alerts ({len(alerts)} pending)")
        table.add_column("ID", style="dim")
        table.add_column("Severity")
        table.add_column("Type")
        table.add_column("Message")
        for a in alerts:
            sev_style = severity_color(a.severity.value)
            table.add_row(
                str(a.id), f"[{sev_style}]{a.severity.value}[/{sev_style}]",
                a.alert_type.replace("_", " "), a.message,
            )
        console.print(table)


@app.command("list")
def list_alerts(
    format: str = typer.Option("rich", "--format", help="Output format: rich, brief, json"),
):
    """List all pending alerts (alias for running 'alerts' with no subcommand)."""
    with get_session() as session:
        alert_svc.generate_alerts(session)
        alerts = alert_svc.list_alerts(session)

        if not alerts:
            console.print("[green]No pending alerts.[/green]")
            return

        if format == "brief":
            for a in alerts:
                print(f"[{a.severity.value}] {a.message}")
            return

        if format == "json":
            import json
            data = [{"id": a.id, "type": a.alert_type, "severity": a.severity.value, "message": a.message} for a in alerts]
            print(json.dumps(data, indent=2))
            return

        table = Table(title=f"Alerts ({len(alerts)} pending)")
        table.add_column("ID", style="dim")
        table.add_column("Severity")
        table.add_column("Type")
        table.add_column("Message")
        for a in alerts:
            sev_style = severity_color(a.severity.value)
            table.add_row(
                str(a.id), f"[{sev_style}]{a.severity.value}[/{sev_style}]",
                a.alert_type.replace("_", " "), a.message,
            )
        console.print(table)


@app.command("snooze")
def snooze_alert(
    alert_id: int = typer.Argument(..., help="Alert ID"),
    days: int = typer.Option(7, "--days", "-d", help="Days to snooze"),
):
    """Snooze an alert for N days."""
    with get_session() as session:
        try:
            alert = alert_svc.snooze_alert(session, alert_id, days)
            format_success(f"Alert {alert_id} snoozed for {days} days")
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)
