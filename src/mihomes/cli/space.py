"""Space CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.services import space as space_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="space", help="Manage spaces (rooms/areas) within properties")


@app.command("add")
def add_space(
    name: str = typer.Argument(..., help="Space name"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    space_type: Optional[str] = typer.Option(None, "--type", "-t", help="Space type (bedroom, kitchen, outdoor, etc.)"),
    description: Optional[str] = typer.Option(None, "--desc", help="Description"),
):
    """Add a space to a property."""
    with get_session() as session:
        try:
            space = space_svc.create_space(session, name, property, space_type=space_type, description=description)
            format_success(f"Space '{space.name}' added to property (slug: {space.slug})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_spaces(
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
):
    """List spaces for a property."""
    with get_session() as session:
        try:
            spaces = space_svc.list_spaces(session, property)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not spaces:
            console.print("[dim]No spaces found for this property.[/dim]")
            return
        table = Table(title="Spaces")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Slug", style="dim")
        for s in spaces:
            table.add_row(str(s.id), s.name, s.space_type or "-", s.slug)
        console.print(table)


@app.command("edit")
def edit_space(
    id_or_slug: str = typer.Argument(..., help="Space ID or slug"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    space_type: Optional[str] = typer.Option(None, "--type", "-t", help="Space type (bedroom, kitchen, outdoor, etc.)"),
    description: Optional[str] = typer.Option(None, "--desc"),
    zone: Optional[str] = typer.Option(None, "--zone", "-z", help="Zone ID or slug"),
):
    """Edit a space."""
    kwargs = {}
    if name is not None: kwargs["name"] = name
    if space_type is not None: kwargs["space_type"] = space_type
    if description is not None: kwargs["description"] = description
    if not kwargs and zone is None:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        if zone is not None:
            from mihomes.models.zone import Zone
            from mihomes.services.slug import resolve_identifier
            z = resolve_identifier(session, Zone, zone)
            kwargs["zone_id"] = z.id
        try:
            space = space_svc.update_space(session, id_or_slug, **kwargs)
            format_success(f"Space '{space.name}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_space(
    id_or_slug: str = typer.Argument(..., help="Space ID or slug"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a space."""
    with get_session() as session:
        try:
            space = space_svc.get_space(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not force:
            typer.confirm(f"Delete space '{space.name}'?", abort=True)
        name = space_svc.delete_space(session, id_or_slug)
        format_success(f"Space '{name}' deleted")
