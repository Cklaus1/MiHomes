"""Property CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import cli_date, console, esc, format_enum, format_panel, format_success, format_error, status_icon
from mihomes.db import get_session
from mihomes.models.property import PropertyStatus, PropertyType
from mihomes.services import property as prop_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError
import logging

logger = logging.getLogger(__name__)

app = typer.Typer(name="property", help="Manage properties")


@app.command("add")
def add_property(
    name: str = typer.Argument(..., help="Property name"),
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Street address"),
    property_type: PropertyType = typer.Option(PropertyType.OTHER, "--type", "-t", help="Property type"),
    climate_zone: Optional[str] = typer.Option(None, "--climate-zone", help="Climate zone for seasonal tasks"),
    sqft: Optional[int] = typer.Option(None, "--sqft", help="Square footage"),
    currency: str = typer.Option("USD", "--currency", help="Default currency"),
    slug: Optional[str] = typer.Option(None, "--slug", help="Custom slug (auto-generated from name if omitted)"),
):
    """Add a new property."""
    with get_session() as session:
        try:
            prop = prop_svc.create_property(
                session,
                name,
                address=address,
                property_type=property_type,
                climate_zone=climate_zone,
                sqft=sqft,
                currency=currency,
                slug=slug,
            )
            format_success(f"Property '{prop.name}' created (slug: {prop.slug})")
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_properties(
    status: Optional[PropertyStatus] = typer.Option(None, "--status", "-s", help="Filter by status"),
    property_type: Optional[PropertyType] = typer.Option(None, "--type", "-t", help="Filter by type"),
):
    """List all properties."""
    with get_session() as session:
        props = prop_svc.list_properties(session, status=status, property_type=property_type)
        if not props:
            console.print("[dim]No properties found.[/dim]")
            return
        table = Table(title="Properties")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Slug", style="dim")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Climate Zone")
        table.add_column("Occupied")
        for p in props:
            table.add_row(
                str(p.id),
                esc(p.name),
                p.slug,
                format_enum(p.property_type),
                f"{status_icon(p.status)} {format_enum(p.status)}",
                esc(p.climate_zone) or "-",
                "Yes" if p.occupied else "No",
            )
        console.print(table)


@app.command("show")
def show_property(
    id_or_slug: str = typer.Argument(..., help="Property ID or slug"),
):
    """Show property details."""
    with get_session() as session:
        try:
            prop = prop_svc.get_property(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        content = {
            "ID": str(prop.id),
            "Slug": prop.slug,
            "Address": prop.address,
            "Type": format_enum(prop.property_type),
            "Status": f"{status_icon(prop.status)} {format_enum(prop.status)}",
            "Climate Zone": prop.climate_zone,
            "Sq Ft": str(prop.sqft) if prop.sqft else None,
            "Currency": prop.currency,
            "Occupied": "Yes" if prop.occupied else "No",
            "Occupied Since": str(prop.occupied_since) if prop.occupied_since else None,
            "Occupied Until": str(prop.occupied_until) if prop.occupied_until else None,
        }
        console.print(format_panel(f"Property: {prop.name}", content))

        if prop.spaces:
            table = Table(title="Spaces")
            table.add_column("Name", style="bold")
            table.add_column("Type")
            table.add_column("Slug", style="dim")
            for s in prop.spaces:
                table.add_row(esc(s.name), esc(s.space_type) or "-", s.slug)
            console.print(table)

        # Show related entities
        from mihomes.models.task import Task, TaskStatus
        from mihomes.models.issue import Issue, IssueStatus
        from mihomes.models.asset import Asset

        open_tasks = session.query(Task).filter(
            Task.property_id == prop.id,
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        ).order_by(Task.due_date.asc().nullslast()).limit(5).all()
        if open_tasks:
            table = Table(title=f"Open Tasks ({len(open_tasks)})")
            table.add_column("Title", style="bold")
            table.add_column("Priority")
            table.add_column("Due")
            for t in open_tasks:
                table.add_row(esc(t.title), t.priority.value, str(t.due_date) if t.due_date else "-")
            console.print(table)

        open_issues = session.query(Issue).filter(
            Issue.property_id == prop.id,
            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        ).order_by(Issue.severity).limit(5).all()
        if open_issues:
            table = Table(title=f"Open Issues ({len(open_issues)})")
            table.add_column("Title", style="bold")
            table.add_column("Severity")
            table.add_column("Status")
            for i in open_issues:
                table.add_row(esc(i.title), i.severity.value, i.status.value)
            console.print(table)

        assets = session.query(Asset).filter(
            Asset.property_id == prop.id, Asset.active.is_(True)
        ).limit(5).all()
        if assets:
            table = Table(title=f"Assets ({len(assets)})")
            table.add_column("Name", style="bold")
            table.add_column("Type")
            table.add_column("Warranty")
            for a in assets:
                table.add_row(esc(a.name), a.asset_type.value, str(a.warranty_expires) if a.warranty_expires else "-")
            console.print(table)

        # Staff assigned
        if prop.staff_members:
            table = Table(title="Staff")
            table.add_column("Name", style="bold")
            table.add_column("Role")
            for s in prop.staff_members:
                table.add_row(esc(s.name), s.role.value)
            console.print(table)

        # Seasonal recommendations for closed/seasonal properties
        from mihomes.models.property import PropertyStatus, PropertyType
        if prop.property_type == PropertyType.SEASONAL or prop.status == PropertyStatus.CLOSED:
            from mihomes.services.seasonal import recommend_seasonal
            try:
                recs = recommend_seasonal(session, id_or_slug)
                if recs:
                    console.print("\n[bold yellow]Seasonal Recommendations:[/bold yellow]")
                    for r in recs[:3]:
                        console.print(f"  → [cyan]mihomes template run {r['template']} --property {prop.slug}[/cyan]")
                        console.print(f"    {r['reason']}")
            except Exception:
                logger.exception("show_property: suppressed exception")


@app.command("edit")
def edit_property(
    id_or_slug: str = typer.Argument(..., help="Property ID or slug"),
    name: Optional[str] = typer.Option(None, "--name"),
    address: Optional[str] = typer.Option(None, "--address"),
    status: Optional[PropertyStatus] = typer.Option(None, "--status"),
    property_type: Optional[PropertyType] = typer.Option(None, "--type"),
    climate_zone: Optional[str] = typer.Option(None, "--climate-zone"),
    sqft: Optional[int] = typer.Option(None, "--sqft"),
    currency: Optional[str] = typer.Option(None, "--currency"),
):
    """Edit a property."""
    kwargs = {}
    if name is not None:
        kwargs["name"] = name
    if address is not None:
        kwargs["address"] = address
    if status is not None:
        kwargs["status"] = status
    if property_type is not None:
        kwargs["property_type"] = property_type
    if climate_zone is not None:
        kwargs["climate_zone"] = climate_zone
    if sqft is not None:
        kwargs["sqft"] = sqft
    if currency is not None:
        kwargs["currency"] = currency

    if not kwargs:
        format_error("No fields to update. Use --name, --address, --status, etc.")
        raise typer.Exit(1)

    with get_session() as session:
        try:
            prop = prop_svc.update_property(session, id_or_slug, **kwargs)
            format_success(f"Property '{prop.name}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_property(
    id_or_slug: str = typer.Argument(..., help="Property ID or slug"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a property."""
    with get_session() as session:
        try:
            prop = prop_svc.get_property(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not force:
            typer.confirm(f"Delete property '{prop.name}' and all its spaces?", abort=True)

        name = prop_svc.delete_property(session, id_or_slug)
        format_success(f"Property '{name}' deleted")


@app.command("occupy")
def occupy_property(
    id_or_slug: str = typer.Argument(..., help="Property ID or slug"),
    from_date: Optional[str] = typer.Option(None, "--from", help="Occupancy start date (YYYY-MM-DD)"),
    until_date: Optional[str] = typer.Option(None, "--to", help="Occupancy end date (YYYY-MM-DD)"),
):
    """Mark a property as occupied."""
    with get_session() as session:
        try:
            fd = cli_date(from_date, "--from")
            ud = cli_date(until_date, "--to")
            prop = prop_svc.occupy_property(session, id_or_slug, fd, ud)
            format_success(f"Property '{prop.name}' marked as occupied")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("vacate")
def vacate_property(
    id_or_slug: str = typer.Argument(..., help="Property ID or slug"),
):
    """Mark a property as unoccupied."""
    with get_session() as session:
        try:
            prop = prop_svc.vacate_property(session, id_or_slug)
            format_success(f"Property '{prop.name}' marked as unoccupied")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("status")
def property_status():
    """Quick overview of all property statuses."""
    with get_session() as session:
        props = prop_svc.list_properties(session)
        if not props:
            console.print("[dim]No properties found.[/dim]")
            return
        for p in props:
            occ = " [green](occupied)[/green]" if p.occupied else ""
            console.print(
                f"  {status_icon(p.status)} [bold]{esc(p.name)}[/bold] — "
                f"{format_enum(p.status)}{occ}"
            )
