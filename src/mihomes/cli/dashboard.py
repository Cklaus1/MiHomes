"""Dashboard CLI — estate overview with Rich layout."""

from typing import Optional

import typer
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mihomes.cli.formatters import console, esc, format_enum, severity_color, status_icon
from mihomes.db import get_session
from mihomes.services.dashboard import get_dashboard_data
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError, resolve_identifier
from mihomes.models.property import Property
import logging

logger = logging.getLogger(__name__)

app = typer.Typer(name="dashboard", help="Estate overview dashboard", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def dashboard(
    ctx: typer.Context,
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Scope to a single property"),
):
    """Show the MiHomes estate dashboard."""
    if ctx.invoked_subcommand is not None:
        return

    with get_session() as session:
        prop_id = None
        if property:
            try:
                prop = resolve_identifier(session, Property, property)
                prop_id = prop.id
            except (AmbiguousIdentifierError, EntityNotFoundError) as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1)

        data = get_dashboard_data(session, property_id=prop_id)

        # Properties panel
        prop_lines = []
        for p in data["properties"]:
            icon = status_icon(p["status"])
            occ = " [green](occupied)[/green]" if p["occupied"] else ""
            issues = f" [red]({p['open_issues']} issues)[/red]" if p["open_issues"] > 0 else ""
            prop_lines.append(f"  {icon} [bold]{esc(p['name'])}[/bold] — {p['status']}{occ}{issues}")
        prop_panel = Panel(
            "\n".join(prop_lines) if prop_lines else "[dim]No properties[/dim]",
            title=f"Properties ({len(data['properties'])})",
            expand=True,
        )

        # Tasks due this week
        task_lines = []
        for t in data["tasks_this_week"][:8]:
            prio_icon = "!" if t.priority.value in ("urgent", "high") else "○"
            task_lines.append(f"  {prio_icon} {esc(t.title)} ({esc(t.property.name)}) — {t.due_date}")
        if data["overdue_count"] > 0:
            task_lines.insert(0, f"  [red bold]! {data['overdue_count']} overdue task(s)[/red bold]")
        if data["unscheduled_count"] > 0:
            task_lines.append(f"\n  [dim]Unscheduled ({data['unscheduled_count']}):[/dim]")
            for t in data["unscheduled_tasks"]:
                assignee = f" → {esc(t.assignee.name)}" if t.assignee else ""
                task_lines.append(f"  [dim]○ {esc(t.title)} ({esc(t.property.name)}){assignee}[/dim]")
        task_panel = Panel(
            "\n".join(task_lines) if task_lines else "[green]No tasks due this week[/green]",
            title=f"Tasks Due This Week ({len(data['tasks_this_week'])})",
            expand=True,
        )

        # Open issues
        issue_lines = []
        for i in data["open_issues"][:6]:
            sev_color = severity_color(i.severity.value)
            issue_lines.append(f"  [{sev_color}]{i.severity.value.upper()}[/{sev_color}] {esc(i.title)} ({esc(i.property.name)})")
        issue_panel = Panel(
            "\n".join(issue_lines) if issue_lines else "[green]No open issues[/green]",
            title=f"Open Issues ({len(data['open_issues'])})",
            expand=True,
        )

        # Budget
        budget_lines = []
        for b in data["budget_summaries"]:
            pct = b["pct_used"]
            bar_len = 20
            filled = int(pct / 100 * bar_len)
            bar_color = "red" if pct > 90 else "yellow" if pct > 75 else "green"
            bar = f"[{bar_color}]{'█' * filled}[/{bar_color}]{'░' * (bar_len - filled)}"
            budget_lines.append(f"  {esc(b['property'])}: {bar} {pct}% (${b['spent']:,.0f} / ${b['budgeted']:,.0f})")
        budget_panel = Panel(
            "\n".join(budget_lines) if budget_lines else "[dim]No budgets set[/dim]",
            title="Budget Status (YTD)",
            expand=True,
        )

        # Calendar / occupancy panel
        from datetime import date as date_cls, datetime, timezone, timedelta
        today = date_cls.today()
        cal_lines = []

        # Occupancy from MiHomes
        for p in data["properties"]:
            since = p["occupied_since"]
            until = p["occupied_until"]
            if p["occupied"]:
                until_str = f"until {until}" if until else "ongoing"
                cal_lines.append(f"  [green]● Occupied[/green]  [bold]{esc(p['name'])}[/bold] — {until_str}")
            elif since and since > today:
                days_away = (since - today).days
                cal_lines.append(f"  [yellow]○ Upcoming[/yellow]  [bold]{esc(p['name'])}[/bold] — {since} ({days_away}d away)")

        # Google Calendar events (next 30 days, excluding MiHomes-pushed ones)
        import os
        if not os.environ.get("MIHOMES_DEMO"):
            try:
                from mihomes.services.calendar_sync import is_google_auth_available, _get_provider
                if is_google_auth_available():
                    now_dt = datetime.now(timezone.utc)
                    gcal_events = _get_provider().list_events(now_dt, now_dt + timedelta(days=30))
                    for ev in gcal_events:
                        title = ev.get("title", "")
                        if title.startswith("[MiHomes]"):
                            continue
                        start = ev.get("start")
                        start_str = start.strftime("%b %d") if start else "-"
                        cal_lines.append(f"  [cyan]◆ Google[/cyan]  {esc(title)} — {start_str}")
            except Exception:
                logger.exception("dashboard: suppressed exception")

        calendar_panel = Panel(
            "\n".join(cal_lines) if cal_lines else "[dim]No active or upcoming occupancy[/dim]",
            title="Calendar / Occupancy",
            expand=True,
        )

        # AI Recommendations panel
        ai_lines = []
        if not os.environ.get("MIHOMES_DEMO"):
            from mihomes.services.ai import orchestrator
            from mihomes.services.ai.provider import AIAuthError, AIProviderError
            try:
                with console.status("[dim]Fetching AI recommendations...[/dim]", spinner="dots"):
                    ai_resp = orchestrator.dashboard_summary(session, property_slug=property)
                space_colors = {"S": "bold red", "P": "bold yellow", "A": "yellow", "C": "cyan", "E": "cyan"}
                for line in ai_resp.text.strip().splitlines():
                    line = line.strip("•- ").strip()
                    if not line:
                        continue
                    first = line[0].upper() if line else ""
                    color = space_colors.get(first)
                    ai_lines.append(f"  [{color}]{line}[/{color}]" if color else f"  {line}")
                ai_lines.append("\n  [dim]Run `mihomes ai review` for full analysis[/dim]")
            except AIAuthError as e:
                logger.warning("dashboard: AI auth failed: %s", e)
                ai_lines = [f"  [yellow]AI unavailable — {e}[/yellow]",
                            "  [dim]Run `mihomes config set ai.api_key ...` to configure.[/dim]"]
            except AIProviderError as e:
                # A real provider/network failure — surface the reason, don't
                # disguise it as "not configured" (M42).
                logger.error("dashboard: AI recommendations failed: %s", e)
                ai_lines = [f"  [yellow]AI recommendations unavailable — {e}[/yellow]"]
            except Exception as e:
                # Unexpected failure: log the full trace and surface the reason.
                logger.exception("dashboard: unexpected AI panel failure")
                ai_lines = [f"  [yellow]AI recommendations unavailable — {e}[/yellow]"]

        ai_panel = Panel(
            "\n".join(ai_lines) if ai_lines else "[dim]AI not configured[/dim]",
            title="AI Recommendations",
            expand=True,
        )

        # Status bar
        status_items = []
        if data["alert_count"] > 0:
            status_items.append(f"[yellow]{data['alert_count']} alert(s)[/yellow]")
        if data["open_work_orders"] > 0:
            status_items.append(f"{data['open_work_orders']} open work order(s)")
        if data["asset_count"] > 0:
            status_items.append(f"{data['asset_count']} tracked asset(s)")
        status_text = "  " + " | ".join(status_items) if status_items else "  [green]All clear[/green]"

        # Render
        today_str = today.strftime("%A, %B %d, %Y")
        title = f"MiHomes Estate Dashboard — {today_str}"
        if property:
            title = f"MiHomes — {esc(property)} — {today_str}"

        console.print()
        console.print(Panel(
            f"{Text()}\n".join([""]),
            title=f"[bold]{title}[/bold]",
            subtitle="mihomes dashboard",
            expand=True,
        ))

        # Layout: properties + tasks | issues + budget | calendar (full width) | status
        console.print(Columns([prop_panel, task_panel], equal=True, expand=True))
        console.print(Columns([issue_panel, budget_panel], equal=True, expand=True))
        console.print(calendar_panel)
        if not os.environ.get("MIHOMES_DEMO"):
            console.print(ai_panel)
        console.print(Panel(status_text, title="Status", expand=True))
        console.print()
