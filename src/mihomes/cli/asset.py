"""Asset CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_panel, format_success
from mihomes.db import get_session
from mihomes.models.asset import AssetCondition, AssetType
from mihomes.services import asset as asset_svc
from mihomes.services.slug import EntityNotFoundError

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
            )
            format_success(f"Asset '{asset.name}' added (slug: {asset.slug}, type: {asset.asset_type.value})")
        except EntityNotFoundError as e:
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
        except EntityNotFoundError as e:
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
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
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
            "Purchase Price": f"{asset.purchase_price:,.2f}" if asset.purchase_price else None,
            "Warranty Expires": str(asset.warranty_expires) if asset.warranty_expires else None,
            "Condition": format_enum(asset.condition),
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
):
    """Edit an asset."""
    from datetime import date
    kwargs = {}
    if name is not None: kwargs["name"] = name
    if condition is not None: kwargs["condition"] = condition
    if notes is not None: kwargs["notes"] = notes
    if warranty_expires is not None: kwargs["warranty_expires"] = date.fromisoformat(warranty_expires)
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            asset = asset_svc.update_asset(session, id_or_slug, **kwargs)
            format_success(f"Asset '{asset.name}' updated")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_asset(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete an asset."""
    with get_session() as session:
        try:
            asset = asset_svc.get_asset(session, id_or_slug)
        except EntityNotFoundError as e:
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
