"""Recurring expense CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.models.recurring_expense import ExpenseFrequency
from mihomes.services import recurring as recurring_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="recurring", help="Manage recurring expenses")


@app.command("add")
def add_recurring(
    name: str = typer.Argument(..., help="Expense name"),
    amount: float = typer.Option(..., "--amount", "-a"),
    frequency: ExpenseFrequency = typer.Option(..., "--frequency", "-f"),
    property: str = typer.Option(..., "--property", "-p"),
    category: str = typer.Option(..., "--category", "-c"),
    vendor: Optional[str] = typer.Option(None, "--vendor", "-v"),
    start: Optional[str] = typer.Option(None, "--start", help="Start date, defaults to today"),
):
    """Add a recurring expense."""
    with get_session() as session:
        try:
            start_date = date.fromisoformat(start) if start else date.today()
            exp = recurring_svc.create_recurring_expense(
                session, name, amount, frequency, property, category, start_date,
                vendor_id_or_slug=vendor,
            )
            format_success(f"Recurring expense '{exp.name}' added (${exp.amount:,.2f} {exp.frequency.value})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_recurring():
    """List all recurring expenses."""
    with get_session() as session:
        expenses = recurring_svc.list_recurring_expenses(session)
        if not expenses:
            console.print("[dim]No recurring expenses.[/dim]")
            return
        table = Table(title="Recurring Expenses")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Amount", justify="right")
        table.add_column("Frequency")
        table.add_column("Property")
        table.add_column("Category")
        table.add_column("Vendor")
        for e in expenses:
            table.add_row(
                str(e.id), e.name, f"{e.currency} {e.amount:,.2f}",
                e.frequency.value, e.property.name, e.category,
                e.vendor.company_name if e.vendor else "-",
            )
        console.print(table)


@app.command("generate")
def generate_transactions():
    """Generate pending transactions for current period."""
    with get_session() as session:
        txs = recurring_svc.generate_transactions(session)
        if txs:
            format_success(f"{len(txs)} transaction(s) generated")
            for tx in txs:
                console.print(f"  ${tx.amount:,.2f} — {tx.description}")
        else:
            console.print("[dim]No transactions due.[/dim]")
