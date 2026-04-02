"""Dashboard CLI — estate overview with Rich layout."""

from typing import Optional

import typer
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mihomes.cli.formatters import console, format_enum, severity_color, status_icon
from mihomes.db import get_session
from mihomes.services.dashboard import get_dashboard_data
from mihomes.services.slug import EntityNotFoundError, resolve_identifier
from mihomes.models.property import Property

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
            except EntityNotFoundError as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1)

        data = get_dashboard_data(session, property_id=prop_id)

        # Properties panel
        prop_lines = []
        for p in data["properties"]:
            icon = status_icon(p["status"])
            occ = " [green](occupied)[/green]" if p["occupied"] else ""
            issues = f" [red]({p['open_issues']} issues)[/red]" if p["open_issues"] > 0 else ""
            prop_lines.append(f"  {icon} [bold]{p['name']}[/bold] — {p['status']}{occ}{issues}")
        prop_panel = Panel(
            "\n".join(prop_lines) if prop_lines else "[dim]No properties[/dim]",
            title=f"Properties ({len(data['properties'])})",
            expand=True,
        )

        # Tasks due this week
        task_lines = []
        for t in data["tasks_this_week"][:8]:
            prio_icon = "!" if t.priority.value in ("urgent", "high") else "○"
            task_lines.append(f"  {prio_icon} {t.title} ({t.property.name}) — {t.due_date}")
        if data["overdue_count"] > 0:
            task_lines.insert(0, f"  [red bold]! {data['overdue_count']} overdue task(s)[/red bold]")
        task_panel = Panel(
            "\n".join(task_lines) if task_lines else "[green]No tasks due this week[/green]",
            title=f"Tasks Due This Week ({len(data['tasks_this_week'])})",
            expand=True,
        )

        # Open issues
        issue_lines = []
        for i in data["open_issues"][:6]:
            sev_color = severity_color(i.severity.value)
            issue_lines.append(f"  [{sev_color}]{i.severity.value.upper()}[/{sev_color}] {i.title} ({i.property.name})")
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
            budget_lines.append(f"  {b['property']}: {bar} {pct}% (${b['spent']:,.0f} / ${b['budgeted']:,.0f})")
        budget_panel = Panel(
            "\n".join(budget_lines) if budget_lines else "[dim]No budgets set[/dim]",
            title="Budget Status (YTD)",
            expand=True,
        )

        # Calendar / occupancy panel
        from datetime import date as date_cls
        today = date_cls.today()
        cal_lines = []
        for p in data["properties"]:
            since = p["occupied_since"]
            until = p["occupied_until"]
            if p["occupied"]:
                until_str = f"until {until}" if until else "ongoing"
                cal_lines.append(f"  [green]● Occupied[/green]  [bold]{p['name']}[/bold] — {until_str}")
            elif since and since > today:
                days_away = (since - today).days
                cal_lines.append(f"  [yellow]○ Upcoming[/yellow]  [bold]{p['name']}[/bold] — {since} ({days_away}d away)")
        calendar_panel = Panel(
            "\n".join(cal_lines) if cal_lines else "[dim]No active or upcoming occupancy[/dim]",
            title="Calendar / Occupancy",
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
            title = f"MiHomes — {property} — {today_str}"

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
        console.print(Panel(status_text, title="Status", expand=True))
        console.print()
