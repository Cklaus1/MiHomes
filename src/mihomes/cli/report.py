"""Report CLI commands — financial reporting and analytics."""

from datetime import date
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mihomes.cli.formatters import console, format_error
from mihomes.db import get_session
from mihomes.services import financial_report as report_svc
from mihomes.services import weekly_report as weekly_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="report", help="Financial reports and analytics")


@app.command("property")
def property_report(
    id_or_slug: str = typer.Argument(..., help="Property ID or slug"),
):
    """Single property health report — tasks, issues, budget, staff, assets, and contracts."""
    from datetime import timedelta
    from rich.panel import Panel
    from rich.columns import Columns
    from mihomes.models.property import Property
    from mihomes.models.task import Task, TaskStatus, TaskPriority
    from mihomes.models.issue import Issue, IssueStatus, IssueSeverity
    from mihomes.models.staff import Staff
    from mihomes.models.asset import Asset
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus
    from mihomes.models.contract import Contract
    from mihomes.models.insurance import InsurancePolicy
    from mihomes.services.slug import resolve_identifier
    from mihomes.services.budget import get_budget_report
    from mihomes.cli.formatters import severity_color

    with get_session() as session:
        try:
            prop = resolve_identifier(session, Property, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        today = date.today()
        year_start = date(today.year, 1, 1)
        year_end = date(today.year + 1, 1, 1)
        soon = today + timedelta(days=60)

        # --- Tasks ---
        all_tasks = session.query(Task).filter(Task.property_id == prop.id).all()
        open_tasks = [t for t in all_tasks if t.status == TaskStatus.PENDING]
        overdue = [t for t in open_tasks if t.due_date and t.due_date < today]
        due_soon = [t for t in open_tasks if t.due_date and today <= t.due_date <= today + timedelta(days=14)]
        completed_ytd = [t for t in all_tasks if t.status == TaskStatus.COMPLETED and t.updated_at and t.updated_at.date() >= year_start]

        # --- Issues ---
        open_issues = session.query(Issue).filter(
            Issue.property_id == prop.id,
            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        ).order_by(Issue.severity).all()

        # --- Budget ---
        budget_rows = get_budget_report(session, str(prop.id), year_start, year_end)

        # --- Staff ---
        staff = session.query(Staff).filter(
            Staff.properties.any(Property.id == prop.id)
        ).all()

        # --- Assets expiring warranty soon ---
        assets_warning = session.query(Asset).filter(
            Asset.property_id == prop.id,
            Asset.active.is_(True),
            Asset.warranty_expires.isnot(None),
            Asset.warranty_expires <= soon,
        ).all()

        # --- Open Work Orders ---
        open_wo = session.query(WorkOrder).filter(
            WorkOrder.property_id == prop.id,
            WorkOrder.status.notin_([WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED, WorkOrderStatus.CANCELLED]),
        ).all()

        # --- Contracts expiring soon ---
        expiring_contracts = session.query(Contract).filter(
            Contract.property_id == prop.id,
            Contract.end_date.isnot(None),
            Contract.end_date >= today,
            Contract.end_date <= soon,
        ).all()

        # --- Insurance renewing soon ---
        expiring_insurance = session.query(InsurancePolicy).filter(
            InsurancePolicy.property_id == prop.id,
            InsurancePolicy.renewal_date.isnot(None),
            InsurancePolicy.renewal_date >= today,
            InsurancePolicy.renewal_date <= soon,
        ).all()

        # ── Header ──
        occ_str = "[green]Occupied[/green]" if prop.occupied else "[dim]Vacant[/dim]"
        status_color = "green" if prop.status.value == "open" else "yellow"
        header_lines = [
            f"Type: {prop.property_type.value.title() if prop.property_type else '-'}  |  "
            f"Status: [{status_color}]{prop.status.value}[/{status_color}]  |  "
            f"Occupancy: {occ_str}",
        ]
        if prop.address:
            header_lines.append(f"Address: {prop.address}")
        if prop.climate_zone:
            header_lines.append(f"Climate zone: {prop.climate_zone}")
        console.print(Panel("\n".join(header_lines), title=f"[bold]{prop.name}[/bold]", expand=True))

        # ── Tasks summary ──
        task_table = Table(title=f"Tasks — {len(open_tasks)} open")
        task_table.add_column("Status", style="bold")
        task_table.add_column("Count", justify="right")
        task_table.add_column("Detail")
        task_table.add_row("[red]Overdue[/red]", str(len(overdue)),
                           ", ".join(t.title[:40] for t in overdue[:3]) + ("…" if len(overdue) > 3 else ""))
        task_table.add_row("[yellow]Due in 14 days[/yellow]", str(len(due_soon)),
                           ", ".join(t.title[:40] for t in due_soon[:3]) + ("…" if len(due_soon) > 3 else ""))
        task_table.add_row("[dim]Completed YTD[/dim]", str(len(completed_ytd)), "")
        console.print(task_table)

        # ── Issues ──
        if open_issues:
            issue_table = Table(title=f"Open Issues ({len(open_issues)})")
            issue_table.add_column("Severity")
            issue_table.add_column("Title", style="bold")
            issue_table.add_column("Status")
            for iss in open_issues[:10]:
                sc = severity_color(iss.severity.value)
                issue_table.add_row(
                    f"[{sc}]{iss.severity.value}[/{sc}]",
                    iss.title,
                    iss.status.value,
                )
            console.print(issue_table)
        else:
            console.print("[green]No open issues.[/green]")

        # ── Budget ──
        if budget_rows:
            budget_table = Table(title=f"Budget — {today.year} YTD")
            budget_table.add_column("Category", style="bold")
            budget_table.add_column("Budgeted", justify="right")
            budget_table.add_column("Spent", justify="right")
            budget_table.add_column("Remaining", justify="right")
            budget_table.add_column("Used %", justify="right")
            for r in budget_rows:
                pct = r["pct_used"]
                pct_color = "red" if pct > 90 else ("yellow" if pct > 75 else "green")
                budget_table.add_row(
                    r["category"],
                    f"${r['budgeted']:,.0f}",
                    f"${r['spent']:,.0f}",
                    f"${r['remaining']:,.0f}",
                    f"[{pct_color}]{pct}%[/{pct_color}]",
                )
            console.print(budget_table)

        # ── Staff ──
        if staff:
            staff_str = "  ".join(f"{s.name} ({s.role.value})" for s in staff)
            console.print(Panel(staff_str, title="Staff", expand=False))

        # ── Work Orders ──
        if open_wo:
            wo_table = Table(title=f"Open Work Orders ({len(open_wo)})")
            wo_table.add_column("Title", style="bold")
            wo_table.add_column("Status")
            wo_table.add_column("Vendor")
            wo_table.add_column("Est. Cost", justify="right")
            for wo in open_wo[:8]:
                vendor = wo.vendor.company_name if wo.vendor else "-"
                cost = f"${wo.estimated_cost:,.0f}" if wo.estimated_cost else "-"
                wo_table.add_row(wo.title, wo.status.value, vendor, cost)
            console.print(wo_table)

        # ── Expiring soon (contracts + insurance + warranties) ──
        expiry_lines = []
        for c in expiring_contracts:
            days_left = (c.end_date - today).days
            expiry_lines.append(f"[yellow]Contract[/yellow] {c.vendor.company_name if c.vendor else '?'} — expires {c.end_date} ({days_left}d)")
        for p in expiring_insurance:
            days_left = (p.renewal_date - today).days
            expiry_lines.append(f"[yellow]Insurance[/yellow] {p.carrier} ({p.insurance_type.value}) — renews {p.renewal_date} ({days_left}d)")
        for a in assets_warning:
            days_left = (a.warranty_expires - today).days
            expiry_lines.append(f"[dim]Warranty[/dim] {a.name} — expires {a.warranty_expires} ({days_left}d)")
        if expiry_lines:
            console.print(Panel("\n".join(expiry_lines), title="Expiring Within 60 Days", expand=False))


@app.command("estate")
def estate_report(
    period: Optional[str] = typer.Option(None, "--period", "-p", help="Period e.g. Q1-2026 or 2026"),
):
    """Full estate summary — properties, tasks, issues, and budget."""
    from rich.panel import Panel
    from mihomes.models.property import Property
    from mihomes.models.task import Task, TaskStatus
    from mihomes.models.issue import Issue, IssueStatus, IssueSeverity
    from mihomes.cli.formatters import severity_color

    with get_session() as session:
        properties = session.query(Property).order_by(Property.name).all()
        if not properties:
            console.print("[dim]No properties found.[/dim]")
            return

        title = f"Estate Report{' — ' + period if period else ''}"
        console.print(f"\n[bold]{title}[/bold]\n")

        for prop in properties:
            pending_tasks = session.query(Task).filter(
                Task.property_id == prop.id,
                Task.status == TaskStatus.PENDING,
            ).count()
            overdue_tasks = session.query(Task).filter(
                Task.property_id == prop.id,
                Task.status == TaskStatus.PENDING,
                Task.due_date < date.today(),
            ).count()
            open_issues = session.query(Issue).filter(
                Issue.property_id == prop.id,
                Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
            ).all()
            critical = sum(1 for i in open_issues if i.severity == IssueSeverity.CRITICAL)
            high = sum(1 for i in open_issues if i.severity == IssueSeverity.HIGH)

            status_color = "green" if prop.status.value == "open" else "yellow"
            lines = [
                f"Status: [{status_color}]{prop.status.value}[/{status_color}]",
                f"Tasks:  {pending_tasks} pending" + (f" ([red]{overdue_tasks} overdue[/red])" if overdue_tasks else ""),
                f"Issues: {len(open_issues)} open" + (
                    f" ([red]{critical} critical[/red], [yellow]{high} high[/yellow])"
                    if critical or high else ""
                ),
            ]
            console.print(Panel("\n".join(lines), title=f"[bold]{prop.name}[/bold]", expand=False))

        # Estate-wide totals
        total_pending = session.query(Task).filter(Task.status == TaskStatus.PENDING).count()
        total_overdue = session.query(Task).filter(
            Task.status == TaskStatus.PENDING, Task.due_date < date.today()
        ).count()
        total_issues = session.query(Issue).filter(
            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED])
        ).count()
        console.print(
            f"\n[dim]Estate totals: {total_pending} pending tasks "
            f"({total_overdue} overdue), {total_issues} open issues[/dim]\n"
        )


@app.command("upcoming")
def upcoming_report(
    days: int = typer.Option(30, "--days", "-d", help="Look-ahead window in days"),
    property: Optional[str] = typer.Option(None, "--property", "-p"),
):
    """Everything due in the next N days — tasks, events, renewals."""
    from datetime import timedelta
    from rich.table import Table
    from mihomes.models.task import Task, TaskStatus
    from mihomes.models.event import Event
    from mihomes.models.contract import Contract
    from mihomes.models.insurance import InsurancePolicy
    from mihomes.models.property import Property

    cutoff = date.today() + timedelta(days=days)
    today = date.today()

    with get_session() as session:
        prop_filter = None
        if property:
            prop_obj = session.query(Property).filter(
                (Property.slug == property) | (Property.id == property)
            ).first()
            if not prop_obj:
                format_error(f"Property '{property}' not found")
                raise typer.Exit(1)
            prop_filter = prop_obj.id

        rows = []

        # Tasks
        q = session.query(Task).filter(
            Task.status == TaskStatus.PENDING,
            Task.due_date != None,
            Task.due_date <= cutoff,
        )
        if prop_filter:
            q = q.filter(Task.property_id == prop_filter)
        for t in q.order_by(Task.due_date).all():
            overdue = t.due_date < today
            due_str = f"[red]{t.due_date} (overdue)[/red]" if overdue else str(t.due_date)
            prop_name = t.property.name if t.property else "-"
            rows.append((t.due_date, due_str, "Task", t.title, prop_name))

        # Events
        q = session.query(Event).filter(
            Event.event_date >= today,
            Event.event_date <= cutoff,
        )
        if prop_filter:
            q = q.filter(Event.property_id == prop_filter)
        for e in q.order_by(Event.event_date).all():
            prop_name = e.property.name if e.property else "-"
            rows.append((e.event_date, str(e.event_date), "Event", e.title, prop_name))

        # Contract renewals
        q = session.query(Contract).filter(
            Contract.end_date != None,
            Contract.end_date >= today,
            Contract.end_date <= cutoff,
        )
        for c in q.order_by(Contract.end_date).all():
            rows.append((c.end_date, str(c.end_date), "Contract Renewal", c.vendor.name if c.vendor else "-", "-"))

        # Insurance renewals
        q = session.query(InsurancePolicy).filter(
            InsurancePolicy.renewal_date != None,
            InsurancePolicy.renewal_date >= today,
            InsurancePolicy.renewal_date <= cutoff,
        )
        for p in q.order_by(InsurancePolicy.renewal_date).all():
            prop_name = p.property.name if p.property else "All"
            rows.append((p.renewal_date, str(p.renewal_date), "Insurance Renewal", p.carrier, prop_name))

        if not rows:
            console.print(f"[dim]Nothing due in the next {days} days.[/dim]")
            return

        rows.sort(key=lambda x: x[0])
        table = Table(title=f"Upcoming — Next {days} Days")
        table.add_column("Date")
        table.add_column("Type", style="dim")
        table.add_column("Title", style="bold")
        table.add_column("Property")
        for _, date_str, kind, title, prop_name in rows:
            table.add_row(date_str, kind, title, prop_name)
        console.print(table)


@app.command("vendor")
def vendor_report(
    id_or_slug: str = typer.Argument(..., help="Vendor ID or slug"),
):
    """Vendor performance summary — work orders, ratings, spending."""
    from rich.panel import Panel
    from mihomes.models.vendor import Vendor
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus
    from mihomes.services.slug import resolve_identifier, EntityNotFoundError
    from mihomes.cli.formatters import format_panel

    with get_session() as session:
        try:
            vendor = resolve_identifier(session, Vendor, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        work_orders = session.query(WorkOrder).filter(WorkOrder.vendor_id == vendor.id).all()
        completed = [w for w in work_orders if w.status == WorkOrderStatus.COMPLETED]
        open_wo = [w for w in work_orders if w.status not in (WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED)]

        total_spent = sum(w.actual_cost or 0 for w in completed)
        total_estimated = sum(w.estimated_cost or 0 for w in completed)

        content = {
            "Categories": vendor.service_categories or "-",
            "Service Area": vendor.service_areas or "-",
            "Phone": vendor.phone or "-",
            "Email": vendor.email or "-",
            "Work Orders": f"{len(work_orders)} total ({len(completed)} completed, {len(open_wo)} open)",
            "Total Spent": f"${total_spent:,.2f}" if total_spent else "-",
            "vs Estimated": f"${total_estimated:,.2f} estimated" if total_estimated else "-",
            "Notes": vendor.notes or "-",
        }
        console.print(format_panel(f"Vendor: {vendor.company_name}", content))

        if completed:
            table = Table(title="Completed Work Orders")
            table.add_column("Title", style="bold")
            table.add_column("Property")
            table.add_column("Estimated", justify="right")
            table.add_column("Actual", justify="right")
            table.add_column("Completed")
            for w in sorted(completed, key=lambda x: x.updated_at or x.created_at, reverse=True)[:10]:
                prop = w.property.name if hasattr(w, 'property') and w.property else "-"
                est = f"${w.estimated_cost:,.0f}" if w.estimated_cost else "-"
                act = f"${w.actual_cost:,.0f}" if w.actual_cost else "-"
                done = str(w.updated_at.date()) if w.updated_at else "-"
                table.add_row(w.title, prop, est, act, done)
            console.print(table)


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
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("vendors")
def vendors_report(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Filter by property slug"),
    start: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Shortcut: filter to a full year"),
):
    """Vendor spending report — expenses + work order costs combined."""
    if year:
        period_start = date(year, 1, 1)
        period_end = date(year, 12, 31)
    else:
        period_start = date.fromisoformat(start) if start else date(date.today().year, 1, 1)
        period_end = date.fromisoformat(end) if end else date.today()

    with get_session() as session:
        try:
            rows = report_svc.vendor_spending_report(session, period_start, period_end, property_id_or_slug=property)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not rows:
            console.print("[dim]No vendor spending found for this period.[/dim]")
            console.print("[dim]Tip: link expenses to vendors with: mihomes expense add <amount> --vendor <slug>[/dim]")
            return

        period_label = f"{period_start} to {period_end}"
        table = Table(title=f"Vendor Spending — {period_label}")
        table.add_column("Vendor", style="bold")
        table.add_column("Expenses", justify="right")
        table.add_column("Exp. $", justify="right")
        table.add_column("Work Orders", justify="right")
        table.add_column("WO $", justify="right")
        table.add_column("Combined Total", justify="right", style="green bold")

        grand_total = sum(r["combined_total"] for r in rows)
        for r in rows:
            pct = r["combined_total"] / grand_total * 100 if grand_total else 0
            tx_str = str(r["tx_count"]) if r["tx_count"] else "-"
            tx_amt = f"${r['tx_total']:,.0f}" if r["tx_total"] else "-"
            wo_str = str(r["wo_count"]) if r["wo_count"] else "-"
            wo_amt = f"${r['wo_total']:,.0f}" if r["wo_total"] else "-"
            table.add_row(
                r["vendor"], tx_str, tx_amt, wo_str, wo_amt,
                f"${r['combined_total']:,.0f}  ({pct:.0f}%)",
            )

        console.print(table)
        console.print(f"\n[bold]Total vendor spend {period_label}: ${grand_total:,.0f}[/bold]")


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
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
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
        f"[dim]{prop_names}[/dim]",
        box=box.ROUNDED,
    ))

    # ── Flags ────────────────────────────────────────────────────────────────
    if data["flags"]:
        console.print("\n[bold red]⚠  Flags[/bold red]")
        for f in data["flags"]:
            console.print(f"  [red]•[/red] {f}")

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
                    task["title"],
                    task["property"],
                    task["assignee"] or "—",
                    task["completed_at"] or "—",
                )
            console.print(t)
        if data["resolved_issues"]:
            console.print(f"  [dim]Issues resolved:[/dim]")
            for i in data["resolved_issues"]:
                console.print(f"    • [{i['severity']}] {i['title']} ({i['property']})")
        if data["completed_work_orders"]:
            console.print(f"  [dim]Work orders completed:[/dim]")
            for w in data["completed_work_orders"]:
                console.print(f"    • {w['title']}")
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
                task["title"],
                task["property"],
                task["priority"],
                f"[{due_style}]{due}[/{due_style}]" if due_style else due,
                task["assignee"] or "—",
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
            t.add_row(task["title"], task["property"], task["due_date"] or "—", task["assignee"] or "—")
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
                issue["title"],
                issue["property"],
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
                task["title"],
                task["property"],
                task["due_date"] or "—",
                f"[{color}]{task['priority']}[/{color}]" if color else task["priority"],
                task["assignee"] or "—",
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
                b["property"],
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
                console.print(f"    • [{i['severity']}] {i['title']} ({i['property']})")
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
        overdue = " ⚠ OVERDUE" if t["due_date"] and t["due_date"] < today.isoformat() else ""
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
        lines.append(
            f"- **{b['property']}**: {b['spent_mtd']:,.0f} / {b['budgeted_mtd']:,.0f} {b['currency']} "
            f"({b['pct_used']}%){over}"
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
    for t in data["in_progress_tasks"]:
        prop = f" [{t['property']}]" if len(data["properties"]) > 1 else ""
        due = f", due {t['due_date']}" if t["due_date"] else ""
        overdue = " ⚠ OVERDUE" if t["due_date"] and t["due_date"] < today.isoformat() else ""
        assignee = f" — {t['assignee']}" if t["assignee"] else ""
        ip_items.append(f"- {t['title']}{prop}{due}{overdue}{assignee}")
    for t in data["overdue_tasks"]:
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
