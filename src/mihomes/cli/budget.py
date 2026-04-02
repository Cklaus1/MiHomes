"""Budget and expense CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.models.budget import BudgetPeriod
from mihomes.services import budget as budget_svc
from mihomes.services.slug import EntityNotFoundError

budget_app = typer.Typer(name="budget", help="Manage budgets")
expense_app = typer.Typer(name="expense", help="Track expenses")


def _parse_period(period_str: str) -> tuple[date, date]:
    """Parse period strings like 'Q1-2026', '2026', '2026-03'."""
    if period_str.startswith("Q"):
        parts = period_str.split("-")
        quarter = int(parts[0][1])
        year = int(parts[1])
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 3
        start = date(year, start_month, 1)
        end = date(year, end_month, 1) if end_month <= 12 else date(year + 1, 1, 1)
        return start, end
    elif len(period_str) == 4:
        year = int(period_str)
        return date(year, 1, 1), date(year + 1, 1, 1)
    elif len(period_str) == 7:
        year, month = period_str.split("-")
        m = int(month)
        start = date(int(year), m, 1)
        end = date(int(year), m + 1, 1) if m < 12 else date(int(year) + 1, 1, 1)
        return start, end
    raise ValueError(f"Cannot parse period: {period_str}")


@budget_app.command("list")
def list_budgets(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
):
    """List all budgets, optionally filtered by property."""
    from mihomes.models.budget import Budget
    from mihomes.models.property import Property as PropertyModel
    with get_session() as session:
        query = session.query(Budget)
        if property:
            try:
                from mihomes.services.slug import resolve_identifier
                prop = resolve_identifier(session, PropertyModel, property)
                query = query.filter(Budget.property_id == prop.id)
            except Exception as e:
                format_error(str(e))
                raise typer.Exit(1)
        rows = [
            (b.id, b.property.name, b.category, b.period.value, b.currency, b.amount, b.period_start)
            for b in query.order_by(Budget.property_id, Budget.category).all()
        ]

    if not rows:
        console.print("[dim]No budgets found.[/dim]")
        return

    table = Table(title="Budgets")
    table.add_column("ID", style="dim")
    table.add_column("Property", style="bold")
    table.add_column("Category")
    table.add_column("Period")
    table.add_column("Amount", justify="right")
    table.add_column("Start")
    for bid, prop_name, category, period, currency, amount, period_start in rows:
        table.add_row(
            str(bid), prop_name, category,
            period, f"{currency} {amount:,.0f}",
            str(period_start),
        )
    console.print(table)


@budget_app.command("set")
def set_budget(
    property: str = typer.Option(..., "--property", "-p"),
    category: str = typer.Option(..., "--category", "-c"),
    amount: float = typer.Option(..., "--amount", "-a"),
    period: BudgetPeriod = typer.Option(BudgetPeriod.ANNUAL, "--period"),
    start: str = typer.Option(None, "--start", help="Period start (YYYY-MM-DD), defaults to current year"),
):
    """Set a budget for a property category."""
    with get_session() as session:
        try:
            period_start = date.fromisoformat(start) if start else date(date.today().year, 1, 1)
            b = budget_svc.set_budget(session, property, category, period, amount, period_start)
            format_success(f"Budget set: {category} = {b.currency} {b.amount:,.0f} ({period.value})")
        except (EntityNotFoundError, ValueError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@budget_app.command("report")
def budget_report(
    property: str = typer.Option(..., "--property", "-p"),
    period: str = typer.Option(None, "--period", help="Period (Q1-2026, 2026, 2026-03)"),
):
    """Show budget vs actual spending."""
    with get_session() as session:
        try:
            if period:
                start, end = _parse_period(period)
            else:
                year = date.today().year
                start, end = date(year, 1, 1), date(year + 1, 1, 1)
            report = budget_svc.get_budget_report(session, property, start, end)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not report:
            console.print("[dim]No budget data for this period.[/dim]")
            return

        table = Table(title=f"Budget Report ({start} to {end})")
        table.add_column("Category", style="bold")
        table.add_column("Budgeted", justify="right")
        table.add_column("Spent", justify="right")
        table.add_column("Remaining", justify="right")
        table.add_column("% Used", justify="right")
        for r in report:
            pct_style = "red" if r["pct_used"] > 90 else "yellow" if r["pct_used"] > 75 else "green"
            remaining_val = f"{r['currency']} {r['remaining']:,.0f}"
            if r["remaining"] < 0:
                remaining_val = f"[red]{remaining_val}[/red]"
            table.add_row(
                r["category"],
                f"{r['currency']} {r['budgeted']:,.0f}",
                f"{r['currency']} {r['spent']:,.0f}",
                remaining_val,
                f"[{pct_style}]{r['pct_used']}%[/{pct_style}]",
            )
        console.print(table)


@expense_app.command("add")
def add_expense(
    amount: float = typer.Argument(..., help="Expense amount"),
    property: str = typer.Option(..., "--property", "-p"),
    category: str = typer.Option(..., "--category", "-c"),
    vendor: Optional[str] = typer.Option(None, "--vendor", "-v"),
    description: Optional[str] = typer.Option(None, "--desc"),
    tx_date: Optional[str] = typer.Option(None, "--date", help="Date (YYYY-MM-DD), defaults to today"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Add an expense."""
    with get_session() as session:
        try:
            d = date.fromisoformat(tx_date) if tx_date else date.today()
            tx = budget_svc.add_transaction(
                session, amount, property, category, d,
                vendor_id_or_slug=vendor, description=description, notes=notes,
            )
            format_success(f"Expense ${tx.amount:,.2f} added ({tx.category})")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@expense_app.command("list")
def list_expenses(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    category: Optional[str] = typer.Option(None, "--category", "-c"),
):
    """List expenses."""
    with get_session() as session:
        try:
            txs = budget_svc.list_transactions(session, property_id_or_slug=property, category=category)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not txs:
            console.print("[dim]No expenses found.[/dim]")
            return

        table = Table(title="Expenses")
        table.add_column("ID", style="dim")
        table.add_column("Date")
        table.add_column("Amount", justify="right", style="bold")
        table.add_column("Category")
        table.add_column("Property")
        table.add_column("Vendor")
        table.add_column("Description")
        for tx in txs:
            table.add_row(
                str(tx.id), str(tx.date),
                f"{tx.currency} {tx.amount:,.2f}",
                tx.category, tx.property.name,
                tx.vendor.company_name if tx.vendor else "-",
                tx.description or "-",
            )
        console.print(table)
