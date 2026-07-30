"""Inventory CLI — consumable stock tracking and reorder management."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, esc, format_error, format_success
from mihomes.db import get_session
from mihomes.models.consumable import ConsumableStatus
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="inventory", help="Manage consumable inventory and reorder lists")


def _status_display(status: ConsumableStatus) -> str:
    return {
        ConsumableStatus.OK: "[green]OK[/green]",
        ConsumableStatus.LOW: "[yellow]Low[/yellow]",
        ConsumableStatus.OUT: "[red]Out[/red]",
        ConsumableStatus.ORDERED: "[cyan]Ordered[/cyan]",
    }.get(status, status.value)


@app.command("list")
def list_inventory(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    reorder: bool = typer.Option(False, "--reorder", "-r", help="Show only items needing reorder"),
):
    """List consumable inventory."""
    from mihomes.services.consumable import list_consumables
    with get_session() as session:
        items = list_consumables(session, property_id_or_slug=property, needs_reorder=reorder)
        if not items:
            console.print("[dim]No inventory items found.[/dim]")
            return
        title = "Reorder List" if reorder else "Consumable Inventory"
        table = Table(title=title)
        table.add_column("Name", style="bold")
        table.add_column("Property")
        table.add_column("Category")
        table.add_column("In Stock")
        table.add_column("To Order")
        table.add_column("Par")
        table.add_column("Unit")
        table.add_column("Status")
        table.add_column("Updated By", style="dim")
        for item in items:
            table.add_row(
                esc(item.name),
                esc(item.property.name),
                esc(item.category) or "-",
                str(item.quantity_in_stock) if item.quantity_in_stock is not None else "-",
                str(item.quantity_to_order) if item.quantity_to_order is not None else "-",
                str(item.par_level) if item.par_level is not None else "-",
                esc(item.unit) or "-",
                _status_display(item.status),
                esc(item.last_updated_by) or "-",
            )
    console.print(table)


@app.command("add")
def add_item(
    name: str = typer.Argument(..., help="Item name"),
    property: str = typer.Option(..., "--property", "-p"),
    unit: Optional[str] = typer.Option(None, "--unit", "-u", help="Unit (bottles, rolls, bags, etc.)"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Category (cleaning, pantry, linens, etc.)"),
    par: Optional[float] = typer.Option(None, "--par", help="Minimum stock level before flagging low"),
    stock: Optional[float] = typer.Option(None, "--stock", help="Current quantity in stock"),
):
    """Add a consumable item to inventory."""
    from mihomes.services.consumable import create_consumable
    with get_session() as session:
        try:
            item = create_consumable(
                session, name, property,
                unit=unit, category=category,
                par_level=par, quantity_in_stock=stock,
            )
            format_success(f"'{item.name}' added to inventory (slug: {item.slug})")
        except (EntityNotFoundError, ValueError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("update")
def update_stock(
    id_or_slug: str = typer.Argument(..., help="Item ID or slug"),
    property: str = typer.Option(..., "--property", "-p"),
    stock: Optional[float] = typer.Option(None, "--stock", help="Current quantity in stock"),
    order: Optional[float] = typer.Option(None, "--order", help="Quantity needed to order"),
    unit: Optional[str] = typer.Option(None, "--unit"),
):
    """Update stock or order quantity for an item."""
    from mihomes.services.consumable import update_stock as svc_update
    with get_session() as session:
        try:
            item = svc_update(
                session, id_or_slug, property,
                quantity_in_stock=stock,
                quantity_to_order=order,
                unit=unit,
            )
            format_success(f"'{item.name}' updated — status: {item.status.value}")
        except (EntityNotFoundError, ValueError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("reorder")
def reorder_list(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
):
    """Show everything that needs to be ordered."""
    from mihomes.services.consumable import get_reorder_list
    with get_session() as session:
        items = get_reorder_list(session, property_id_or_slug=property)
        if not items:
            console.print("[green]Nothing needs reordering.[/green]")
            return
        table = Table(title="Needs Ordering")
        table.add_column("Item", style="bold")
        table.add_column("Property")
        table.add_column("Status")
        table.add_column("In Stock")
        table.add_column("To Order")
        table.add_column("Unit")
        table.add_column("Reported By", style="dim")
        for item in items:
            table.add_row(
                esc(item.name),
                esc(item.property.name),
                _status_display(item.status),
                str(item.quantity_in_stock) if item.quantity_in_stock is not None else "-",
                str(item.quantity_to_order) if item.quantity_to_order is not None else "-",
                esc(item.unit) or "-",
                esc(item.last_updated_by) or "-",
            )
    console.print(table)


@app.command("mark-ordered")
def mark_ordered(
    id_or_slug: str = typer.Argument(..., help="Item ID or slug"),
):
    """Mark an item as ordered (clears to-order quantity)."""
    from mihomes.services.consumable import mark_ordered as svc_mark
    with get_session() as session:
        try:
            item = svc_mark(session, id_or_slug)
            format_success(f"'{item.name}' marked as ordered")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("mark-restocked")
def mark_restocked(
    id_or_slug: str = typer.Argument(..., help="Item ID or slug"),
    quantity: Optional[float] = typer.Option(None, "--quantity", "-q", help="New quantity in stock"),
):
    """Mark an item as restocked."""
    from mihomes.services.consumable import mark_restocked as svc_restock
    with get_session() as session:
        try:
            item = svc_restock(session, id_or_slug, quantity=quantity)
            format_success(f"'{item.name}' restocked — status: {item.status.value}")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
