"""Asset CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_panel, format_success
from mihomes.db import get_session
from mihomes.models.asset import AssetCondition, AssetType
from mihomes.services import asset as asset_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="asset", help="Track and manage property assets")


@app.command("add")
def add_asset(
    name: str = typer.Argument(..., help="Asset name"),
    asset_type: AssetType = typer.Option(..., "--type", "-t", help="Asset type"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    space: Optional[str] = typer.Option(None, "--space", help="Space ID or slug"),
    make: Optional[str] = typer.Option(None, "--make"),
    model_name: Optional[str] = typer.Option(None, "--model"),
    serial: Optional[str] = typer.Option(None, "--serial"),
    purchase_date: Optional[str] = typer.Option(None, "--purchased", help="Purchase date YYYY-MM-DD"),
    purchase_price: Optional[float] = typer.Option(None, "--price"),
    warranty_expires: Optional[str] = typer.Option(None, "--warranty", help="Warranty expiry YYYY-MM-DD"),
    condition: AssetCondition = typer.Option(AssetCondition.GOOD, "--condition"),
    notes: Optional[str] = typer.Option(None, "--notes"),
    install_date: Optional[str] = typer.Option(None, "--installed", help="Install date YYYY-MM-DD (if different from purchase)"),
    lifespan: Optional[float] = typer.Option(None, "--lifespan", help="Expected lifespan in years (e.g. 15)"),
    replacement_cost: Optional[float] = typer.Option(None, "--replacement-cost", help="Estimated replacement cost"),
    last_serviced: Optional[str] = typer.Option(None, "--last-serviced", help="Last service date YYYY-MM-DD"),
):
    """Add a new asset."""
    from datetime import date
    with get_session() as session:
        try:
            asset = asset_svc.create_asset(
                session, name, asset_type, property,
                space_id_or_slug=space, make=make, model_name=model_name,
                serial_number=serial,
                purchase_date=date.fromisoformat(purchase_date) if purchase_date else None,
                purchase_price=purchase_price,
                warranty_expires=date.fromisoformat(warranty_expires) if warranty_expires else None,
                condition=condition, notes=notes,
                install_date=date.fromisoformat(install_date) if install_date else None,
                expected_lifespan_years=lifespan,
                replacement_cost_estimate=replacement_cost,
                last_serviced=date.fromisoformat(last_serviced) if last_serviced else None,
            )
            format_success(f"Asset '{asset.name}' added (slug: {asset.slug}, type: {asset.asset_type.value})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_assets(
    asset_type: Optional[AssetType] = typer.Option(None, "--type", "-t"),
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    warranty_expiring: Optional[int] = typer.Option(None, "--warranty-expiring", help="Show assets with warranty expiring in N days"),
):
    """List assets."""
    with get_session() as session:
        try:
            if warranty_expiring is not None:
                assets = asset_svc.list_by_warranty_expiring(session, days=warranty_expiring)
            else:
                assets = asset_svc.list_assets(
                    session, property_id_or_slug=property, asset_type=asset_type,
                )
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not assets:
            console.print("[dim]No assets found.[/dim]")
            return

        table = Table(title="Assets")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Property")
        table.add_column("Condition")
        table.add_column("Warranty Expires")
        table.add_column("Slug", style="dim")
        for a in assets:
            table.add_row(
                str(a.id), a.name, format_enum(a.asset_type),
                a.property.name, format_enum(a.condition),
                str(a.warranty_expires) if a.warranty_expires else "-",
                a.slug,
            )
        console.print(table)


@app.command("show")
def show_asset(id_or_slug: str = typer.Argument(...)):
    """Show asset details."""
    with get_session() as session:
        try:
            asset = asset_svc.get_asset(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        lc = asset_svc.get_lifecycle_data(asset)
        content = {
            "ID": str(asset.id),
            "Slug": asset.slug,
            "Type": format_enum(asset.asset_type),
            "Property": asset.property.name,
            "Space": asset.space.name if asset.space else None,
            "Make": asset.make,
            "Model": asset.model_name,
            "Serial Number": asset.serial_number,
            "Purchase Date": str(asset.purchase_date) if asset.purchase_date else None,
            "Install Date": str(asset.install_date) if asset.install_date else None,
            "Purchase Price": f"${asset.purchase_price:,.2f}" if asset.purchase_price else None,
            "Warranty Expires": str(asset.warranty_expires) if asset.warranty_expires else None,
            "Last Serviced": str(asset.last_serviced) if asset.last_serviced else None,
            "Condition": format_enum(asset.condition),
            "Expected Lifespan": f"{asset.expected_lifespan_years:.0f} years" if asset.expected_lifespan_years else None,
            "Age": f"{lc['age_years']} years" if lc["age_years"] is not None else None,
            "Remaining Life": f"{lc['remaining_years']} years ({100 - lc['pct_life_used']:.0f}%)" if lc["remaining_years"] is not None else None,
            "Est. EOL": str(lc["expected_eol"]) if lc["expected_eol"] else None,
            "Replacement Cost Est.": f"${asset.replacement_cost_estimate:,.0f}" if asset.replacement_cost_estimate else None,
            "Notes": asset.notes,
            "Active": "Yes" if asset.active else "No",
        }
        console.print(format_panel(f"Asset: {asset.name}", content))


@app.command("edit")
def edit_asset(
    id_or_slug: str = typer.Argument(...),
    name: Optional[str] = typer.Option(None, "--name"),
    condition: Optional[AssetCondition] = typer.Option(None, "--condition"),
    notes: Optional[str] = typer.Option(None, "--notes"),
    warranty_expires: Optional[str] = typer.Option(None, "--warranty", help="Warranty expiry YYYY-MM-DD"),
    install_date: Optional[str] = typer.Option(None, "--installed", help="Install date YYYY-MM-DD"),
    lifespan: Optional[float] = typer.Option(None, "--lifespan", help="Expected lifespan in years"),
    replacement_cost: Optional[float] = typer.Option(None, "--replacement-cost", help="Estimated replacement cost"),
    last_serviced: Optional[str] = typer.Option(None, "--last-serviced", help="Last service date YYYY-MM-DD"),
):
    """Edit an asset."""
    from datetime import date
    kwargs = {}
    if name is not None: kwargs["name"] = name
    if condition is not None: kwargs["condition"] = condition
    if notes is not None: kwargs["notes"] = notes
    if warranty_expires is not None: kwargs["warranty_expires"] = date.fromisoformat(warranty_expires)
    if install_date is not None: kwargs["install_date"] = date.fromisoformat(install_date)
    if lifespan is not None: kwargs["expected_lifespan_years"] = lifespan
    if replacement_cost is not None: kwargs["replacement_cost_estimate"] = replacement_cost
    if last_serviced is not None: kwargs["last_serviced"] = date.fromisoformat(last_serviced)
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            asset = asset_svc.update_asset(session, id_or_slug, **kwargs)
            format_success(f"Asset '{asset.name}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("lifecycle")
def lifecycle_view(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Filter by property slug"),
):
    """Show asset health and remaining lifespan for all tracked assets."""
    from datetime import date
    with get_session() as session:
        try:
            assets = asset_svc.list_lifecycle_assets(session, property_id_or_slug=property)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not assets:
            console.print("[dim]No assets with lifespan data found. Add --lifespan when creating assets.[/dim]")
            return

        rows = []
        for a in assets:
            lc = asset_svc.get_lifecycle_data(a)
            rows.append({
                "name": a.name,
                "property": a.property.name,
                "lifespan": a.expected_lifespan_years,
                "replacement_cost": a.replacement_cost_estimate,
                **lc,
            })

    table = Table(title="Asset Lifecycle Health")
    table.add_column("Asset", style="bold")
    table.add_column("Property")
    table.add_column("Age")
    table.add_column("Lifespan")
    table.add_column("Remaining")
    table.add_column("Health", justify="right")
    table.add_column("Est. EOL")
    table.add_column("Replace Cost")

    today = date.today()
    for r in rows:
        pct_used = r["pct_life_used"] or 0
        pct_left = 100 - pct_used
        eol = r["expected_eol"]

        if pct_left <= 10 or (eol and eol <= today):
            health_str = f"[bold red]{pct_left:.0f}%[/bold red]"
            eol_str = f"[bold red]{eol}[/bold red]" if eol else "-"
        elif pct_left <= 25:
            health_str = f"[yellow]{pct_left:.0f}%[/yellow]"
            eol_str = f"[yellow]{eol}[/yellow]" if eol else "-"
        elif pct_left <= 50:
            health_str = f"[cyan]{pct_left:.0f}%[/cyan]"
            eol_str = str(eol) if eol else "-"
        else:
            health_str = f"[green]{pct_left:.0f}%[/green]"
            eol_str = str(eol) if eol else "-"

        table.add_row(
            r["name"], r["property"],
            f"{r['age_years']}y" if r["age_years"] is not None else "-",
            f"{r['lifespan']:.0f}y",
            f"{r['remaining_years']}y" if r["remaining_years"] is not None else "-",
            health_str, eol_str,
            f"${r['replacement_cost']:,.0f}" if r["replacement_cost"] else "-",
        )
    console.print(table)


@app.command("capital-plan")
def capital_plan(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Filter by property slug"),
    years: int = typer.Option(5, "--years", "-y", help="Planning horizon in years"),
):
    """Show capital replacement forecast — what needs replacing and when."""
    from datetime import date
    from collections import defaultdict

    with get_session() as session:
        try:
            results = asset_svc.list_capital_plan(session, years=years, property_id_or_slug=property)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not results:
            console.print(f"[dim]No assets reaching end-of-life within {years} years.[/dim]")
            return

        rows = []
        for a, lc in results:
            rows.append({
                "name": a.name,
                "property": a.property.name,
                "asset_type": format_enum(a.asset_type),
                "replacement_cost": a.replacement_cost_estimate,
                **lc,
            })

    today = date.today()
    by_year: dict = defaultdict(list)
    for r in rows:
        by_year[r["expected_eol"].year].append(r)

    total_cost = sum(r["replacement_cost"] for r in rows if r["replacement_cost"])

    for year in sorted(by_year.keys()):
        year_rows = by_year[year]
        year_cost = sum(r["replacement_cost"] for r in year_rows if r["replacement_cost"])
        label = f"{year}" + (" [bold red](this year)[/bold red]" if year == today.year else "")
        table = Table(title=f"[bold]{label}[/bold] — {len(year_rows)} asset(s)  |  Est. ${year_cost:,.0f}")
        table.add_column("Asset", style="bold")
        table.add_column("Property")
        table.add_column("Type")
        table.add_column("Age")
        table.add_column("Remaining")
        table.add_column("Replacement Cost")

        for r in year_rows:
            remaining = r["remaining_years"]
            rem_str = f"{remaining}y" if remaining is not None else "-"
            if remaining is not None and remaining < 0:
                rem_str = f"[bold red]OVERDUE {abs(remaining):.1f}y[/bold red]"
            elif remaining is not None and remaining < 1:
                rem_str = f"[red]{rem_str}[/red]"

            table.add_row(
                r["name"], r["property"], r["asset_type"],
                f"{r['age_years']}y" if r["age_years"] is not None else "-",
                rem_str,
                f"${r['replacement_cost']:,.0f}" if r["replacement_cost"] else "—",
            )
        console.print(table)
        console.print()

    console.print(f"[bold]Total estimated capital expenditure ({years}-year horizon): ${total_cost:,.0f}[/bold]")


@app.command("delete")
def delete_asset(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete an asset."""
    with get_session() as session:
        try:
            asset = asset_svc.get_asset(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete asset '{asset.name}'?", abort=True)
        name = asset_svc.delete_asset(session, id_or_slug)
        format_success(f"Asset '{name}' deleted")


@app.command("vehicle")
def list_vehicles():
    """List all vehicle assets (shortcut for --type vehicle)."""
    with get_session() as session:
        assets = asset_svc.list_by_type(session, AssetType.VEHICLE)
        if not assets:
            console.print("[dim]No vehicles found.[/dim]")
            return
        table = Table(title="Vehicles")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Make")
        table.add_column("Model")
        table.add_column("Property")
        table.add_column("Condition")
        table.add_column("Slug", style="dim")
        for a in assets:
            table.add_row(
                str(a.id), a.name, a.make or "-", a.model_name or "-",
                a.property.name, format_enum(a.condition), a.slug,
            )
        console.print(table)
