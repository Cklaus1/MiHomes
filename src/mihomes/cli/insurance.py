"""Insurance CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_success
from mihomes.db import get_session
from mihomes.models.insurance import InsuranceType
from mihomes.services import insurance as ins_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

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
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("show")
def show_policy(id_or_slug: str = typer.Argument(..., help="Policy ID")):
    """Show insurance policy details."""
    from mihomes.cli.formatters import format_panel
    with get_session() as session:
        try:
            policy_id = int(id_or_slug)
        except ValueError:
            format_error(f"Policy ID must be a number. Got: '{id_or_slug}'")
            raise typer.Exit(1)
        from mihomes.models.insurance import InsurancePolicy
        p = session.get(InsurancePolicy, policy_id)
        if not p:
            format_error(f"Policy #{policy_id} not found")
            raise typer.Exit(1)
        content = {
            "ID": str(p.id),
            "Carrier": p.carrier,
            "Type": format_enum(p.insurance_type),
            "Policy Number": p.policy_number or "-",
            "Property": p.property.name if p.property else "All Properties",
            "Coverage Limit": f"{p.currency} {p.coverage_limit:,.0f}" if p.coverage_limit else "-",
            "Deductible": f"{p.currency} {p.deductible:,.0f}" if p.deductible else "-",
            "Annual Premium": f"{p.currency} {p.annual_premium:,.0f}" if p.annual_premium else "-",
            "Renewal Date": str(p.renewal_date) if p.renewal_date else "-",
            "Agent": p.agent_contact or "-",
            "Notes": p.notes or "-",
        }
        console.print(format_panel(f"Policy: {p.carrier}", content))


@app.command("edit")
def edit_policy(
    policy_id: int = typer.Argument(..., help="Policy ID"),
    carrier: Optional[str] = typer.Option(None, "--carrier"),
    coverage: Optional[float] = typer.Option(None, "--coverage"),
    deductible: Optional[float] = typer.Option(None, "--deductible"),
    premium: Optional[float] = typer.Option(None, "--premium"),
    renewal: Optional[str] = typer.Option(None, "--renewal", help="Renewal date YYYY-MM-DD"),
    policy_number: Optional[str] = typer.Option(None, "--policy-number"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Edit an insurance policy."""
    from datetime import date
    kwargs = {}
    if carrier is not None: kwargs["carrier"] = carrier
    if coverage is not None: kwargs["coverage_limit"] = coverage
    if deductible is not None: kwargs["deductible"] = deductible
    if premium is not None: kwargs["annual_premium"] = premium
    if renewal is not None: kwargs["renewal_date"] = date.fromisoformat(renewal)
    if policy_number is not None: kwargs["policy_number"] = policy_number
    if notes is not None: kwargs["notes"] = notes
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            p = ins_svc.update_policy(session, policy_id, **kwargs)
            format_success(f"Policy #{p.id} ({p.carrier}) updated")
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("gaps")
def coverage_gaps():
    """AI review of insurance coverage adequacy across the estate."""
    from mihomes.services.ai.provider import AIAuthError, AIProviderError
    from mihomes.services.ai.orchestrator import ask
    from rich.markdown import Markdown
    with get_session() as session:
        policies = ins_svc.list_policies(session)
        if not policies:
            console.print("[dim]No insurance policies found. Add policies with: mihomes insurance add[/dim]")
            return

        policy_summary = "\n".join(
            f"- {p.carrier} ({p.insurance_type.value}): "
            f"coverage={p.coverage_limit or 'unknown'}, "
            f"premium={p.annual_premium or 'unknown'}, "
            f"renewal={p.renewal_date or 'unknown'}, "
            f"property={p.property.name if p.property else 'all'}"
            for p in policies
        )
        query = (
            f"Review these insurance policies for coverage gaps and adequacy:\n\n{policy_summary}\n\n"
            "Identify: missing coverage types, underinsured properties, policies expiring soon, "
            "and any coverage gaps given what you know about the estate's properties and assets. "
            "Use the Compliance and Asset Protection dimensions of SPACE."
        )
        try:
            with console.status("[bold blue]Analyzing coverage...", spinner="dots"):
                response = ask(session, query, role="compliance")
            console.print()
            console.print(Markdown(response.text))
            console.print()
        except (AIAuthError, AIProviderError) as e:
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
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
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
