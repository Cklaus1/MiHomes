"""Insurance CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_success
from mihomes.db import get_session
from mihomes.models.insurance import InsuranceType
from mihomes.services import insurance as ins_svc
from mihomes.services.slug import EntityNotFoundError

app = typer.Typer(name="insurance", help="Track insurance policies")


@app.command("add")
def add_policy(
    carrier: str = typer.Option(..., "--carrier"),
    insurance_type: InsuranceType = typer.Option(..., "--type", "-t"),
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    policy_number: Optional[str] = typer.Option(None, "--policy-number"),
    coverage: Optional[float] = typer.Option(None, "--coverage"),
    deductible: Optional[float] = typer.Option(None, "--deductible"),
    premium: Optional[float] = typer.Option(None, "--premium"),
    renewal: Optional[str] = typer.Option(None, "--renewal", help="Renewal date YYYY-MM-DD"),
):
    """Add an insurance policy."""
    with get_session() as session:
        try:
            policy = ins_svc.create_policy(
                session, carrier, insurance_type,
                policy_number=policy_number,
                coverage_limit=coverage, deductible=deductible,
                annual_premium=premium, property_id_or_slug=property,
                renewal_date=date.fromisoformat(renewal) if renewal else None,
            )
            format_success(f"Insurance policy added: {policy.carrier} ({format_enum(policy.insurance_type)})")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_policies(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    expiring: Optional[int] = typer.Option(None, "--expiring", help="Policies expiring in N days"),
):
    """List insurance policies."""
    with get_session() as session:
        try:
            policies = ins_svc.list_policies(session, property_id_or_slug=property, expiring_days=expiring)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not policies:
            console.print("[dim]No policies found.[/dim]")
            return
        table = Table(title="Insurance Policies")
        table.add_column("ID", style="dim")
        table.add_column("Carrier", style="bold")
        table.add_column("Type")
        table.add_column("Property")
        table.add_column("Coverage", justify="right")
        table.add_column("Premium", justify="right")
        table.add_column("Renewal")
        for p in policies:
            table.add_row(
                str(p.id), p.carrier, format_enum(p.insurance_type),
                p.property.name if p.property else "-",
                f"{p.currency} {p.coverage_limit:,.0f}" if p.coverage_limit else "-",
                f"{p.currency} {p.annual_premium:,.0f}" if p.annual_premium else "-",
                str(p.renewal_date) if p.renewal_date else "-",
            )
        console.print(table)
