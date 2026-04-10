"""Automation CLI commands — scheduled operations, digests, escalation."""

from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success, severity_color
from mihomes.db import get_session
from mihomes.services import automation as auto_svc

app = typer.Typer(name="auto", help="Automation and scheduled operations")


@app.command("digest")
def daily_digest(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    format: str = typer.Option("rich", "--format", help="Output: rich, brief"),
):
    """Generate daily digest of tasks, issues, and alerts."""
    with get_session() as session:
        digest = auto_svc.generate_daily_digest(session, property_slug=property)

        if format == "brief":
            print(auto_svc.format_digest_brief(digest))
            return

        console.print(Panel(f"[bold]Daily Digest — {digest['date']}[/bold]", expand=True))

        if digest["overdue_tasks"]:
            table = Table(title=f"Overdue Tasks ({len(digest['overdue_tasks'])})", style="red")
            table.add_column("Task")
            table.add_column("Property")
            table.add_column("Days Overdue", justify="right")
            for t in digest["overdue_tasks"]:
                table.add_row(t["title"], t["property"], str(t["days_overdue"]))
            console.print(table)

        if digest["tasks_due_today"]:
            table = Table(title=f"Due Today ({len(digest['tasks_due_today'])})")
            table.add_column("Task")
            table.add_column("Property")
            table.add_column("Priority")
            for t in digest["tasks_due_today"]:
                pstyle = severity_color(t["priority"])
                table.add_row(t["title"], t["property"], f"[{pstyle}]{t['priority']}[/{pstyle}]")
            console.print(table)

        if digest["critical_issues"]:
            table = Table(title=f"Critical/High Issues ({len(digest['critical_issues'])})")
            table.add_column("Issue")
            table.add_column("Property")
            table.add_column("Severity")
            for i in digest["critical_issues"]:
                sstyle = severity_color(i["severity"])
                table.add_row(i["title"], i["property"], f"[{sstyle}]{i['severity']}[/{sstyle}]")
            console.print(table)

        if not any([digest["overdue_tasks"], digest["tasks_due_today"], digest["critical_issues"]]):
            console.print("[green]All clear — no urgent items today.[/green]")

        if digest["alert_count"]:
            console.print(f"\n[yellow]{digest['alert_count']} pending alert(s) — run 'mihomes alerts'[/yellow]")
        console.print()


@app.command("escalate")
def escalate_tasks(
    days: int = typer.Option(7, "--days", "-d", help="Escalate tasks overdue by more than N days"),
):
    """Escalate priority of long-overdue tasks."""
    with get_session() as session:
        count = auto_svc.escalate_overdue_tasks(session, escalate_after_days=days)
        if count:
            format_success(f"{count} task(s) escalated to higher priority")
        else:
            console.print("[dim]No tasks to escalate.[/dim]")


@app.command("check-expirations")
def check_expirations(
    days: int = typer.Option(30, "--days", "-d", help="Check for items expiring within N days"),
):
    """Generate alerts for expiring contracts, insurance, and warranties."""
    with get_session() as session:
        count = auto_svc.generate_expiration_alerts(session, days_ahead=days)
        if count:
            format_success(f"{count} new expiration alert(s) generated")
        else:
            console.print("[dim]No new expirations to flag.[/dim]")


@app.command("run-all")
def run_all():
    """Run all automation checks (digest, escalation, expiration alerts, WhatsApp extraction)."""
    with get_session() as session:
        escalated = auto_svc.escalate_overdue_tasks(session)
        expiration_alerts = auto_svc.generate_expiration_alerts(session)

        from mihomes.services.alerts import generate_alerts
        task_alerts = generate_alerts(session)

        # WhatsApp extraction — silently skips if bridge is not running
        wa_issues = 0
        wa_tasks = 0
        wa_status = "bridge not running"
        try:
            from mihomes.services.gateways.whatsapp.extractor import extract_and_create
            from mihomes.services.gateways.whatsapp.client import WhatsAppBridgeError
            wa_result = extract_and_create(session)
            wa_issues = wa_result["issues_created"]
            wa_tasks = wa_result["tasks_created"]
            wa_status = f"{wa_issues} issue(s), {wa_tasks} task(s) from {wa_result['messages_processed']} messages"
        except Exception:
            pass

        weather_alerts = auto_svc.run_weather_alerts(session)
        weather_suggestions = auto_svc.run_weather_task_suggestions(session)

        console.print("[bold]Automation run complete:[/bold]")
        console.print(f"  - {escalated} task(s) escalated")
        console.print(f"  - {expiration_alerts} expiration alert(s) generated")
        console.print(f"  - {task_alerts} task/issue alert(s) generated")
        console.print(f"  - {weather_alerts} weather alert(s) generated")
        console.print(f"  - {weather_suggestions} propert(ies) with AI weather task suggestions")
        console.print(f"  - WhatsApp: {wa_status}")

        digest = auto_svc.generate_daily_digest(session)
        console.print()
        print(auto_svc.format_digest_brief(digest))
