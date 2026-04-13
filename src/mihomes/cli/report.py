"""Report CLI commands — financial reporting and analytics."""

from datetime import date
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mihomes.cli.formatters import console, format_error
from mihomes.db import get_session
from mihomes.services import financial_report as report_svc
from mihomes.services import weekly_report as weekly_svc
from mihomes.services.slug import EntityNotFoundError

app = typer.Typer(name="report", help="Financial reports and analytics")


@app.command("spending")
def spending_report(
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    by: str = typer.Option("category", "--by", "-b", help="Group by: category or vendor"),
    start: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
):
    """Show spending report grouped by category or vendor."""
    period_start = date.fromisoformat(start) if start else date(date.today().year, 1, 1)
    period_end = date.fromisoformat(end) if end else date.today()
    with get_session() as session:
        try:
            if by == "vendor":
                rows = report_svc.spending_by_vendor(session, property, period_start, period_end)
                table = Table(title=f"Spending by Vendor ({period_start} to {period_end})")
                table.add_column("Vendor", style="bold")
                table.add_column("Transactions", justify="right")
                table.add_column("Total", justify="right", style="green")
                for r in rows:
                    table.add_row(r["vendor"], str(r["transaction_count"]), f"{r['total']:,.2f}")
            else:
                rows = report_svc.spending_by_category(session, property, period_start, period_end)
                table = Table(title=f"Spending by Category ({period_start} to {period_end})")
                table.add_column("Category", style="bold")
                table.add_column("Transactions", justify="right")
                table.add_column("Total", justify="right", style="green")
                for r in rows:
                    table.add_row(r["category"], str(r["transaction_count"]), f"{r['total']:,.2f}")
            console.print(table)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("compare")
def compare_properties(
    start: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
):
    """Compare spending across all properties."""
    period_start = date.fromisoformat(start) if start else date(date.today().year, 1, 1)
    period_end = date.fromisoformat(end) if end else date.today()
    with get_session() as session:
        rows = report_svc.property_comparison(session, period_start, period_end)
        if not rows:
            console.print("[dim]No spending data found.[/dim]")
            return
        table = Table(title=f"Property Spending Comparison ({period_start} to {period_end})")
        table.add_column("Property", style="bold")
        table.add_column("Transactions", justify="right")
        table.add_column("Total Spending", justify="right", style="green")
        table.add_column("Currency")
        for r in rows:
            table.add_row(
                r["property"], str(r["transaction_count"]),
                f"{r['total_spending']:,.2f}", r["currency"],
            )
        console.print(table)


@app.command("forecast")
def forecast_spending(
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    months: int = typer.Option(6, "--months", "-m", help="Number of months to forecast"),
):
    """Forecast future spending based on historical data."""
    with get_session() as session:
        try:
            result = report_svc.forecast(session, property, months=months)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)

        console.print(f"\n[bold]Spending Forecast: {result['property']}[/bold]")
        console.print(f"Historical period: {result['historical_period']}")
        console.print(f"Monthly average: [green]{result['monthly_average']:,.2f}[/green]")
        console.print(f"Forecast ({result['forecast_months']} months): [bold green]{result['forecast_total']:,.2f}[/bold green]")

        if result["by_category"]:
            console.print()
            table = Table(title="Forecast by Category")
            table.add_column("Category", style="bold")
            table.add_column("Monthly Avg", justify="right")
            table.add_column(f"{months}-Month Forecast", justify="right", style="green")
            for cat in result["by_category"]:
                table.add_row(
                    cat["category"],
                    f"{cat['monthly_avg']:,.2f}",
                    f"{cat['forecast_total']:,.2f}",
                )
            console.print(table)


@app.command("weekly")
def weekly_report(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Property slug or 'all' (default: all)"),
    format: str = typer.Option("terminal", "--format", "-f", help="Output format: terminal, markdown, or 15-5"),
):
    """Weekly operational report — tasks, issues, budget, flags. Designed for EA Monday review."""
    with get_session() as session:
        try:
            data = weekly_svc.generate(session, property_slug=property)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)

    if format == "markdown":
        _print_markdown(data)
    elif format == "15-5":
        _print_15_5(data)
    else:
        _print_terminal(data)


# ── Terminal renderer ─────────────────────────────────────────────────────────

def _print_terminal(data: dict) -> None:
    from datetime import date
    today = date.today()
    prop_names = ", ".join(p["name"] for p in data["properties"]) or "All Properties"
    console.print()
    console.print(Panel(
        f"[bold]Weekly Report[/bold]  ·  {data['period']['from']} → {data['period']['to']}\n"
        f"[dim]{escape(prop_names)}[/dim]",
        box=box.ROUNDED,
    ))

    # ── Flags ────────────────────────────────────────────────────────────────
    if data["flags"]:
        console.print("\n[bold red]⚠  Flags[/bold red]")
        for f in data["flags"]:
            console.print(f"  [red]•[/red] {escape(f)}")

    # ── Done this week ───────────────────────────────────────────────────────
    console.print(f"\n[bold green]✅  Done This Week[/bold green]")
    if data["completed_tasks"] or data["resolved_issues"] or data["completed_work_orders"]:
        if data["completed_tasks"]:
            t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
            t.add_column("Task", style="green")
            t.add_column("Property", style="dim")
            t.add_column("Assignee", style="dim")
            t.add_column("Done", style="dim", justify="right")
            for task in data["completed_tasks"]:
                t.add_row(
                    escape(task["title"]),
                    escape(task["property"]),
                    escape(task["assignee"]) if task["assignee"] else "—",
                    task["completed_at"] or "—",
                )
            console.print(t)
        if data["resolved_issues"]:
            console.print(f"  [dim]Issues resolved:[/dim]")
            for i in data["resolved_issues"]:
                console.print(f"    • [[{i['severity']}]] {escape(i['title'])} ({escape(i['property'])})")
        if data["completed_work_orders"]:
            console.print(f"  [dim]Work orders completed:[/dim]")
            for w in data["completed_work_orders"]:
                console.print(f"    • {escape(w['title'])}")
    else:
        console.print("  [dim]No completed tasks or resolved issues.[/dim]")

    # ── In progress ──────────────────────────────────────────────────────────
    console.print(f"\n[bold yellow]🔨  In Progress[/bold yellow]")
    if data["in_progress_tasks"]:
        t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        t.add_column("Task")
        t.add_column("Property", style="dim")
        t.add_column("Priority", style="dim")
        t.add_column("Due", style="dim", justify="right")
        t.add_column("Assignee", style="dim")
        for task in data["in_progress_tasks"]:
            due = task["due_date"] or "—"
            overdue = task["due_date"] and date.fromisoformat(task["due_date"]) < today
            due_style = "red" if overdue else ""
            t.add_row(
                escape(task["title"]),
                escape(task["property"]),
                task["priority"],
                f"[{due_style}]{due}[/{due_style}]" if due_style else due,
                escape(task["assignee"]) if task["assignee"] else "—",
            )
        console.print(t)
    else:
        console.print("  [dim]Nothing actively in progress.[/dim]")

    # ── Overdue ──────────────────────────────────────────────────────────────
    if data["overdue_tasks"]:
        console.print(f"\n[bold red]🚨  Overdue ({len(data['overdue_tasks'])})[/bold red]")
        t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        t.add_column("Task", style="red")
        t.add_column("Property", style="dim")
        t.add_column("Due", style="red", justify="right")
        t.add_column("Assignee", style="dim")
        for task in data["overdue_tasks"]:
            t.add_row(escape(task["title"]), escape(task["property"]), task["due_date"] or "—", escape(task["assignee"]) if task["assignee"] else "—")
        console.print(t)

    # ── Open issues ──────────────────────────────────────────────────────────
    if data["open_issues"]:
        console.print(f"\n[bold]🔴  Open Issues ({len(data['open_issues'])})[/bold]")
        t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        t.add_column("Issue")
        t.add_column("Property", style="dim")
        t.add_column("Severity", justify="center")
        t.add_column("Status", style="dim")
        sev_colors = {"critical": "red", "high": "yellow", "medium": "blue", "low": "dim"}
        for issue in data["open_issues"]:
            color = sev_colors.get(issue["severity"], "")
            t.add_row(
                escape(issue["title"]),
                escape(issue["property"]),
                f"[{color}]{issue['severity']}[/{color}]",
                issue["status"],
            )
        console.print(t)

    # ── Upcoming ─────────────────────────────────────────────────────────────
    console.print(f"\n[bold]📅  Upcoming (next 7 days)[/bold]")
    if data["upcoming_tasks"]:
        t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        t.add_column("Task")
        t.add_column("Property", style="dim")
        t.add_column("Due", justify="right")
        t.add_column("Priority", style="dim")
        t.add_column("Assignee", style="dim")
        pri_colors = {"urgent": "red", "high": "yellow", "medium": "", "low": "dim"}
        for task in data["upcoming_tasks"]:
            color = pri_colors.get(task["priority"], "")
            t.add_row(
                escape(task["title"]),
                escape(task["property"]),
                task["due_date"] or "—",
                f"[{color}]{task['priority']}[/{color}]" if color else task["priority"],
                escape(task["assignee"]) if task["assignee"] else "—",
            )
        console.print(t)
    else:
        console.print("  [dim]No tasks due in the next 7 days.[/dim]")

    # ── Budget MTD ───────────────────────────────────────────────────────────
    console.print(f"\n[bold]💰  Budget MTD[/bold]")
    if any(b["budgeted_mtd"] > 0 for b in data["budget"]):
        t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        t.add_column("Property", style="bold")
        t.add_column("Budgeted", justify="right")
        t.add_column("Spent MTD", justify="right")
        t.add_column("This Week", justify="right", style="dim")
        t.add_column("Used", justify="right")
        for b in data["budget"]:
            if b["budgeted_mtd"] == 0:
                continue
            pct = f"{b['pct_used']}%" if b["pct_used"] is not None else "—"
            style = "red" if b["over_budget"] else ""
            t.add_row(
                escape(b["property"]),
                f"{b['budgeted_mtd']:,.0f}",
                f"[{style}]{b['spent_mtd']:,.0f}[/{style}]" if style else f"{b['spent_mtd']:,.0f}",
                f"{b['spent_this_week']:,.0f}",
                f"[{style}]{pct}[/{style}]" if style else pct,
            )
        console.print(t)
    else:
        console.print("  [dim]No budget data for this period.[/dim]")

    # ── New this week ─────────────────────────────────────────────────────────
    new_count = len(data["new_issues"]) + len(data["new_work_orders"])
    if new_count:
        console.print(f"\n[bold dim]New This Week[/bold dim]")
        if data["new_issues"]:
            console.print(f"  Issues opened: {len(data['new_issues'])}")
            for i in data["new_issues"]:
                console.print(f"    • [[{i['severity']}]] {escape(i['title'])} ({escape(i['property'])})")
        if data["new_work_orders"]:
            console.print(f"  Work orders opened: {len(data['new_work_orders'])}")

    console.print()


# ── Markdown renderer ─────────────────────────────────────────────────────────

def _print_markdown(data: dict) -> None:
    from datetime import date
    today = date.today()
    prop_names = ", ".join(p["name"] for p in data["properties"]) or "All Properties"

    lines = [
        f"# Weekly Report — {data['period']['from']} to {data['period']['to']}",
        f"**Properties**: {prop_names}  ",
        f"**Generated**: {data['generated_at'][:16].replace('T', ' ')}",
        "",
    ]

    if data["flags"]:
        lines += ["## ⚠ Flags", ""]
        for f in data["flags"]:
            lines.append(f"- {f}")
        lines.append("")

    lines += ["## ✅ Done This Week", ""]
    if data["completed_tasks"]:
        for t in data["completed_tasks"]:
            assignee = f" — {t['assignee']}" if t["assignee"] else ""
            lines.append(f"- **{t['title']}** ({t['property']}){assignee}")
    if data["resolved_issues"]:
        for i in data["resolved_issues"]:
            lines.append(f"- Issue resolved: [{i['severity']}] {i['title']} ({i['property']})")
    if data["completed_work_orders"]:
        for w in data["completed_work_orders"]:
            lines.append(f"- Work order closed: {w['title']}")
    if not (data["completed_tasks"] or data["resolved_issues"] or data["completed_work_orders"]):
        lines.append("_No completions this week._")
    lines.append("")

    lines += ["## 🔨 In Progress", ""]
    for t in data["in_progress_tasks"]:
        due = f", due {t['due_date']}" if t["due_date"] else ""
        overdue = " ⚠ OVERDUE" if t["due_date"] and date.fromisoformat(t["due_date"]) < today else ""
        lines.append(f"- **{t['title']}** ({t['property']}){due}{overdue}")
    if not data["in_progress_tasks"]:
        lines.append("_Nothing actively in progress._")
    lines.append("")

    if data["overdue_tasks"]:
        lines += [f"## 🚨 Overdue ({len(data['overdue_tasks'])})", ""]
        for t in data["overdue_tasks"]:
            assignee = f" — {t['assignee']}" if t["assignee"] else ""
            lines.append(f"- **{t['title']}** ({t['property']}) — due {t['due_date']}{assignee}")
        lines.append("")

    if data["open_issues"]:
        lines += [f"## 🔴 Open Issues ({len(data['open_issues'])})", ""]
        for i in data["open_issues"]:
            lines.append(f"- [{i['severity'].upper()}] **{i['title']}** ({i['property']}) — {i['status']}")
        lines.append("")

    lines += ["## 📅 Upcoming (next 7 days)", ""]
    for t in data["upcoming_tasks"]:
        assignee = f" — {t['assignee']}" if t["assignee"] else ""
        lines.append(f"- **{t['title']}** ({t['property']}) — due {t['due_date']}{assignee}")
    if not data["upcoming_tasks"]:
        lines.append("_No tasks due in the next 7 days._")
    lines.append("")

    lines += ["## 💰 Budget MTD", ""]
    for b in data["budget"]:
        if b["budgeted_mtd"] == 0:
            continue
        over = " ⚠ OVER BUDGET" if b["over_budget"] else ""
        pct = f"{b['pct_used']}%" if b["pct_used"] is not None else "—"
        lines.append(
            f"- **{b['property']}**: {b['spent_mtd']:,.0f} / {b['budgeted_mtd']:,.0f} {b['currency']} "
            f"({pct}){over}"
        )
    lines.append("")

    print("\n".join(lines))


# ── 15-5 renderer ─────────────────────────────────────────────────────────────

def _print_15_5(data: dict) -> None:
    """
    Outputs a pre-filled 15-5 report draft in the exact template format.
    Millena reviews, adds narrative/flags, and sends to Chris.
    """
    from datetime import date, timedelta
    today = date.today()
    # Find the Monday of this week for the header
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    prop_names = ", ".join(p["name"] for p in data["properties"]) or "All Properties"

    lines = [
        f"📍 {prop_names}",
        f"📅 Week of {monday.strftime('%b %d')} – {friday.strftime('%b %d, %Y')}",
        f"👤 Millena",
        "",
    ]

    # ── ✅ Done this week ─────────────────────────────────────────────────────
    lines += ["✅ Done this week:"]

    done_items = []
    for t in data["completed_tasks"]:
        prop = f" [{t['property']}]" if len(data["properties"]) > 1 else ""
        assignee = f" — {t['assignee']}" if t["assignee"] else ""
        done_items.append(f"- {t['title']}{prop}{assignee}")
    for i in data["resolved_issues"]:
        prop = f" [{i['property']}]" if len(data["properties"]) > 1 else ""
        done_items.append(f"- Issue resolved: {i['title']}{prop}")
    for w in data["completed_work_orders"]:
        done_items.append(f"- Work order closed: {w['title']}")

    if done_items:
        lines += done_items
    else:
        lines.append("- (nothing completed this week — add context if needed)")
    lines.append("")

    # ── 🔨 In progress ────────────────────────────────────────────────────────
    lines += ["🔨 In progress:"]

    ip_items = []
    in_progress_ids = {t["id"] for t in data["in_progress_tasks"]}
    for t in data["in_progress_tasks"]:
        prop = f" [{t['property']}]" if len(data["properties"]) > 1 else ""
        due = f", due {t['due_date']}" if t["due_date"] else ""
        overdue = " ⚠ OVERDUE" if t["due_date"] and date.fromisoformat(t["due_date"]) < today else ""
        assignee = f" — {t['assignee']}" if t["assignee"] else ""
        ip_items.append(f"- {t['title']}{prop}{due}{overdue}{assignee}")
    for t in data["overdue_tasks"]:
        if t["id"] in in_progress_ids:
            continue  # already shown above with ⚠ OVERDUE marker
        prop = f" [{t['property']}]" if len(data["properties"]) > 1 else ""
        assignee = f" — {t['assignee']}" if t["assignee"] else ""
        ip_items.append(f"- ⚠ OVERDUE: {t['title']}{prop} (was due {t['due_date']}){assignee}")

    if ip_items:
        lines += ip_items
    else:
        lines.append("- (nothing actively in progress)")
    lines.append("")

    # ── 📅 Plan for next week ─────────────────────────────────────────────────
    lines += ["📅 Plan for next week:"]

    upcoming = data["upcoming_tasks"]
    if upcoming:
        for t in upcoming[:7]:  # cap at 7 to keep it readable
            prop = f" [{t['property']}]" if len(data["properties"]) > 1 else ""
            assignee = f" — {t['assignee']}" if t["assignee"] else ""
            lines.append(f"- {t['title']}{prop} (due {t['due_date']}){assignee}")
        if len(upcoming) > 7:
            lines.append(f"- ...and {len(upcoming) - 7} more")
    else:
        lines.append("- (no tasks scheduled — add any planned work here)")
    lines.append("")

    # ── 🎯 My priorities ──────────────────────────────────────────────────────
    lines += [
        "🎯 My priorities:",
        "- ",
        "- ",
        "- ",
        "",
    ]

    # ── 🚩 Flags ──────────────────────────────────────────────────────────────
    all_flags = list(data["flags"])
    # Millena's own flags/blockers added below the auto-generated ones

    # Add open issues as flags if critical/high
    critical_high = [i for i in data["open_issues"] if i["severity"] in ("critical", "high")]
    for i in critical_high:
        prop = f" [{i['property']}]" if len(data["properties"]) > 1 else ""
        flag = f"[{i['severity'].upper()}] Open issue: {i['title']}{prop} — {i['status']}"
        if flag not in all_flags:
            all_flags.append(flag)

    lines += ["🚩 Flags / Blockers:"]
    if all_flags:
        for f in all_flags:
            lines.append(f"- [auto] {f}")
    lines += [
        "- ",
        "- ",
    ]
    lines.append("")

    lines += [
        "❓ Needs decision from Chris:",
        "- ",
        "",
    ]

    # ── 💰 Budget snapshot ────────────────────────────────────────────────────
    budget_lines = [
        b for b in data["budget"]
        if b["budgeted_mtd"] > 0 or b["spent_mtd"] > 0
    ]
    if budget_lines:
        lines += ["💰 Budget MTD:"]
        for b in budget_lines:
            pct = f"{b['pct_used']}%" if b["pct_used"] is not None else "—"
            over = " ⚠ OVER" if b["over_budget"] else ""
            spent_wk = f"  (this week: {b['spent_this_week']:,.0f})" if b["spent_this_week"] else ""
            lines.append(
                f"- {b['property']}: {b['spent_mtd']:,.0f} / {b['budgeted_mtd']:,.0f} {b['currency']} ({pct}){over}{spent_wk}"
            )
        lines.append("")

    # ── ⏱ Time split ──────────────────────────────────────────────────────────
    lines += [
        "⏱ Time split (rough %):",
        "- Operations / staff oversight: __%",
        "- Vendor coordination: __%",
        "- Admin / reporting: __%",
        "- Other: __%",
        "",
        "---",
        f"_Generated {data['generated_at'][:16].replace('T', ' ')} · mihomes report weekly --format 15-5_",
    ]

    print("\n".join(lines))
