"""Contract CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.services import contract as contract_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="contract", help="Manage vendor contracts")


@app.command("add")
def add_contract(
    vendor: str = typer.Option(..., "--vendor", "-v"),
    property: str = typer.Option(..., "--property", "-p"),
    start: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
    annual: Optional[float] = typer.Option(None, "--annual", help="Annual cost"),
    auto_renew: bool = typer.Option(False, "--auto-renew"),
    category: Optional[str] = typer.Option(None, "--category"),
):
    """Add a vendor contract."""
    with get_session() as session:
        try:
            c = contract_svc.create_contract(
                session, vendor, property, date.fromisoformat(start),
                end_date=date.fromisoformat(end) if end else None,
                annual_cost=annual, auto_renew=auto_renew,
                service_category=category,
            )
            format_success(f"Contract #{c.id} created ({c.vendor.company_name} → {c.property.name})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("show")
def show_contract(id_or_slug: str = typer.Argument(..., help="Contract ID")):
    """Show contract details."""
    from mihomes.cli.formatters import format_panel
    with get_session() as session:
        try:
            contract_id = int(id_or_slug)
        except ValueError:
            format_error(f"Contract ID must be a number. Got: '{id_or_slug}'")
            raise typer.Exit(1)
        from mihomes.models.contract import Contract
        c = session.get(Contract, contract_id)
        if not c:
            format_error(f"Contract #{contract_id} not found")
            raise typer.Exit(1)
        content = {
            "ID": str(c.id),
            "Vendor": c.vendor.company_name,
            "Property": c.property.name,
            "Category": c.service_category or "-",
            "Start Date": str(c.start_date),
            "End Date": str(c.end_date) if c.end_date else "Ongoing",
            "Annual Cost": f"{c.currency} {c.annual_cost:,.0f}" if c.annual_cost else "-",
            "Auto-Renew": "Yes" if c.auto_renew else "No",
            "Notice Period": f"{c.notice_period_days} days",
            "Notes": c.notes or "-",
        }
        console.print(format_panel(f"Contract #{c.id}", content))


@app.command("list")
def list_contracts(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    expiring: Optional[int] = typer.Option(None, "--expiring", help="Show contracts expiring in N days"),
):
    """List contracts."""
    with get_session() as session:
        try:
            contracts = contract_svc.list_contracts(session, property_id_or_slug=property, expiring_days=expiring)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not contracts:
            console.print("[dim]No contracts found.[/dim]")
            return
        table = Table(title="Contracts")
        table.add_column("ID", style="dim")
        table.add_column("Vendor", style="bold")
        table.add_column("Property")
        table.add_column("Category")
        table.add_column("Start")
        table.add_column("End")
        table.add_column("Annual Cost", justify="right")
        table.add_column("Auto-Renew")
        for c in contracts:
            table.add_row(
                str(c.id), c.vendor.company_name, c.property.name,
                c.service_category or "-", str(c.start_date),
                str(c.end_date) if c.end_date else "ongoing",
                f"{c.currency} {c.annual_cost:,.0f}" if c.annual_cost else "-",
                "Yes" if c.auto_renew else "No",
            )
        console.print(table)
