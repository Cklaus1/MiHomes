"""Report CLI commands — financial reporting and analytics."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error
from mihomes.db import get_session
from mihomes.services import financial_report as report_svc
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


@app.command("weekly")
def weekly_report(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Scope to one property (slug or ID)"),
    format: str = typer.Option("rich", "--format", "-f", help="Output format: rich or markdown"),
):
    """Weekly estate status report — flags, done, in-progress, overdue, issues, upcoming, budget MTD.

    Examples:\n
      mihomes report weekly\n
      mihomes report weekly --property miami\n
      mihomes report weekly --format markdown
    """
    from datetime import timedelta
    from mihomes.models.property import Property
    from mihomes.models.task import Task, TaskStatus
    from mihomes.models.issue import Issue, IssueStatus, IssueSeverity
    from mihomes.models.budget import Budget, Transaction
    from mihomes.cli.formatters import severity_color
    from mihomes.services.budget import get_budget_report

    today = date.today()
    week_ago = today - timedelta(days=7)
    week_ahead = today + timedelta(days=7)
    month_start = date(today.year, today.month, 1)
    md = format.lower() == "markdown"

    with get_session() as session:
        # Resolve properties to report on
        if property:
            from mihomes.services.slug import resolve_identifier
            try:
                props = [resolve_identifier(session, Property, property)]
            except Exception:
                format_error(f"Property not found: {property}")
                raise typer.Exit(1)
        else:
            props = session.query(Property).order_by(Property.name).all()

        if not props:
            console.print("[dim]No properties found.[/dim]")
            return

        prop_ids = [p.id for p in props]

        # ── Gather data ──────────────────────────────────────────────────
        all_tasks = session.query(Task).filter(Task.property_id.in_(prop_ids)).all()
        pending   = [t for t in all_tasks if t.status == TaskStatus.PENDING]
        overdue   = [t for t in pending if t.due_date and t.due_date < today]
        upcoming  = [t for t in pending if t.due_date and today <= t.due_date <= week_ahead]
        in_prog   = [t for t in pending if t.due_date is None or t.due_date >= today]
        done_week = [t for t in all_tasks
                     if t.status == TaskStatus.COMPLETED
                     and t.updated_at and t.updated_at.date() >= week_ago]

        open_issues = session.query(Issue).filter(
            Issue.property_id.in_(prop_ids),
            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        ).order_by(Issue.severity).all()
        resolved_week = session.query(Issue).filter(
            Issue.property_id.in_(prop_ids),
            Issue.status.in_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
            Issue.updated_at >= week_ago,
        ).all()

        critical_issues = [i for i in open_issues if i.severity == IssueSeverity.CRITICAL]
        high_issues     = [i for i in open_issues if i.severity == IssueSeverity.HIGH]

        # Budget MTD per property
        budget_by_prop = {}
        for prop in props:
            rows = get_budget_report(session, str(prop.id), month_start, today)
            budget_by_prop[prop.id] = (prop.name, rows)

        # ── Flags (only shown if there are problems) ─────────────────────
        flags = []
        if overdue:
            flags.append(f"{len(overdue)} overdue task(s)")
        if critical_issues:
            flags.append(f"{len(critical_issues)} critical issue(s)")
        if high_issues:
            flags.append(f"{len(high_issues)} high-priority issue(s)")
        for prop_name, rows in budget_by_prop.values():
            for r in rows:
                if r.get("pct_used", 0) > 100:
                    flags.append(f"{prop_name} over budget: {r['category']} ({r['pct_used']:.0f}%)")

        # ── Render ───────────────────────────────────────────────────────
        report_title = f"Weekly Estate Report — {today}"
        if property and props:
            report_title = f"Weekly Report: {props[0].name} — {today}"

        if md:
            _weekly_markdown(
                report_title, flags, done_week, resolved_week,
                in_prog, overdue, open_issues, upcoming, budget_by_prop, today,
            )
        else:
            _weekly_rich(
                report_title, flags, done_week, resolved_week,
                in_prog, overdue, open_issues, upcoming, budget_by_prop, today,
            )


def _weekly_rich(title, flags, done_week, resolved_week, in_prog, overdue, open_issues, upcoming, budget_by_prop, today):
    """Render the weekly report using Rich tables/panels."""
    from rich.panel import Panel
    from mihomes.cli.formatters import severity_color

    console.print(f"\n[bold]{title}[/bold]\n")

    # ⚠ Flags
    if flags:
        flag_lines = "\n".join(f"  • {f}" for f in flags)
        console.print(Panel(flag_lines, title="[bold yellow]⚠ Flags[/bold yellow]", expand=False))

    # ✅ Done this week
    done_all = done_week + resolved_week
    if done_all:
        table = Table(title=f"✅ Done This Week ({len(done_all)})", show_header=True)
        table.add_column("Type", style="dim", width=8)
        table.add_column("Title", style="bold")
        table.add_column("Property")
        for t in done_week:
            prop_name = t.property.name if t.property else "-"
            table.add_row("Task", t.title, prop_name)
        for i in resolved_week:
            prop_name = i.property.name if i.property else "-"
            table.add_row("Issue", i.title, prop_name)
        console.print(table)

    # 🔨 In Progress
    if in_prog:
        table = Table(title=f"🔨 In Progress ({len(in_prog)})", show_header=True)
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Due")
        for t in sorted(in_prog, key=lambda x: x.due_date or date.max)[:20]:
            prop_name = t.property.name if t.property else "-"
            due = str(t.due_date) if t.due_date else "-"
            table.add_row(t.title, prop_name, due)
        console.print(table)

    # 🚨 Overdue
    if overdue:
        table = Table(title=f"[red]🚨 Overdue ({len(overdue)})[/red]", show_header=True)
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Due", style="red")
        table.add_column("Days", justify="right", style="red")
        for t in sorted(overdue, key=lambda x: x.due_date):
            prop_name = t.property.name if t.property else "-"
            days = (today - t.due_date).days
            table.add_row(t.title, prop_name, str(t.due_date), f"{days}d")
        console.print(table)

    # 🔴 Open Issues
    if open_issues:
        table = Table(title=f"🔴 Open Issues ({len(open_issues)})", show_header=True)
        table.add_column("Severity", width=10)
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Status")
        for i in open_issues[:20]:
            sc = severity_color(i.severity.value)
            prop_name = i.property.name if i.property else "-"
            table.add_row(
                f"[{sc}]{i.severity.value}[/{sc}]",
                i.title, prop_name, i.status.value,
            )
        console.print(table)

    # 📅 Upcoming (next 7 days)
    if upcoming:
        table = Table(title=f"📅 Upcoming — Next 7 Days ({len(upcoming)})", show_header=True)
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Due")
        for t in sorted(upcoming, key=lambda x: x.due_date):
            prop_name = t.property.name if t.property else "-"
            table.add_row(t.title, prop_name, str(t.due_date))
        console.print(table)

    # 💰 Budget MTD
    for prop_id, (prop_name, rows) in budget_by_prop.items():
        if not rows:
            continue
        table = Table(title=f"💰 Budget MTD — {prop_name}", show_header=True)
        table.add_column("Category", style="bold")
        table.add_column("Budgeted", justify="right")
        table.add_column("Spent", justify="right")
        table.add_column("Remaining", justify="right")
        table.add_column("%", justify="right")
        for r in rows:
            pct = r.get("pct_used", 0)
            pct_color = "red" if pct > 100 else ("yellow" if pct > 75 else "green")
            table.add_row(
                r["category"],
                f"${r['budgeted']:,.0f}",
                f"${r['spent']:,.0f}",
                f"${r['remaining']:,.0f}",
                f"[{pct_color}]{pct:.0f}%[/{pct_color}]",
            )
        console.print(table)


def _weekly_markdown(title, flags, done_week, resolved_week, in_prog, overdue, open_issues, upcoming, budget_by_prop, today):
    """Render the weekly report as plain markdown."""
    from mihomes.cli.formatters import severity_color

    lines = [f"# {title}", ""]

    # ⚠ Flags
    if flags:
        lines += ["## ⚠ Flags", ""]
        for f in flags:
            lines.append(f"- {f}")
        lines.append("")

    # ✅ Done
    done_all = done_week + resolved_week
    if done_all:
        lines += [f"## ✅ Done This Week ({len(done_all)})", ""]
        for t in done_week:
            prop_name = t.property.name if t.property else "-"
            lines.append(f"- **[Task]** {t.title} — {prop_name}")
        for i in resolved_week:
            prop_name = i.property.name if i.property else "-"
            lines.append(f"- **[Issue]** {i.title} — {prop_name}")
        lines.append("")

    # 🔨 In Progress
    if in_prog:
        lines += [f"## 🔨 In Progress ({len(in_prog)})", ""]
        for t in sorted(in_prog, key=lambda x: x.due_date or date.max)[:20]:
            prop_name = t.property.name if t.property else "-"
            due = f" _(due {t.due_date})_" if t.due_date else ""
            lines.append(f"- {t.title} — {prop_name}{due}")
        lines.append("")

    # 🚨 Overdue
    if overdue:
        lines += [f"## 🚨 Overdue ({len(overdue)})", ""]
        for t in sorted(overdue, key=lambda x: x.due_date):
            prop_name = t.property.name if t.property else "-"
            days = (today - t.due_date).days
            lines.append(f"- **{t.title}** — {prop_name} _(was due {t.due_date}, {days}d ago)_")
        lines.append("")

    # 🔴 Open Issues
    if open_issues:
        lines += [f"## 🔴 Open Issues ({len(open_issues)})", ""]
        for i in open_issues[:20]:
            prop_name = i.property.name if i.property else "-"
            lines.append(f"- **[{i.severity.value.upper()}]** {i.title} — {prop_name} _{i.status.value}_")
        lines.append("")

    # 📅 Upcoming
    if upcoming:
        lines += [f"## 📅 Upcoming — Next 7 Days ({len(upcoming)})", ""]
        for t in sorted(upcoming, key=lambda x: x.due_date):
            prop_name = t.property.name if t.property else "-"
            lines.append(f"- {t.title} — {prop_name} _(due {t.due_date})_")
        lines.append("")

    # 💰 Budget MTD
    for prop_id, (prop_name, rows) in budget_by_prop.items():
        if not rows:
            continue
        lines += [f"## 💰 Budget MTD — {prop_name}", ""]
        lines.append("| Category | Budgeted | Spent | Remaining | % |")
        lines.append("|---|---:|---:|---:|---:|")
        for r in rows:
            pct = r.get("pct_used", 0)
            flag = " 🔴" if pct > 100 else (" 🟡" if pct > 75 else "")
            lines.append(
                f"| {r['category']} | ${r['budgeted']:,.0f} | ${r['spent']:,.0f} "
                f"| ${r['remaining']:,.0f} | {pct:.0f}%{flag} |"
            )
        lines.append("")

    console.print("\n".join(lines))


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
