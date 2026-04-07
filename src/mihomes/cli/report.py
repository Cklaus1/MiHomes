"""Report CLI commands — financial reporting and analytics."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error
from mihomes.db import get_session
from mihomes.services import financial_report as report_svc
from mihomes.services.slug import EntityNotFoundError

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
        except EntityNotFoundError as e:
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
        except EntityNotFoundError as e:
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
