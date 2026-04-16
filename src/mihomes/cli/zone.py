"""Zone CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_panel, format_success, severity_color, status_icon, format_enum
from mihomes.db import get_session
from mihomes.services import zone as zone_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="zone", help="Manage property zones (Upstairs, Exterior Back, etc.)")


@app.command("add")
def add_zone(
    name: str = typer.Argument(..., help="Zone name"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    description: Optional[str] = typer.Option(None, "--desc"),
):
    """Add a zone to a property."""
    with get_session() as session:
        try:
            zone = zone_svc.create_zone(session, name, property, description=description)
            format_success(f"Zone '{zone.name}' added (slug: {zone.slug})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_zones(
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
):
    """List all zones for a property."""
    with get_session() as session:
        try:
            zones = zone_svc.list_zones(session, property)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not zones:
            console.print("[dim]No zones found. Add one with: mihomes zone add[/dim]")
            return

        table = Table(title="Zones")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Spaces", justify="right")
        table.add_column("Open Tasks", justify="right")
        table.add_column("Slug", style="dim")
        for z in zones:
            table.add_row(
                str(z.id), z.name,
                str(len(z.spaces)),
                str(sum(1 for t in z.tasks if t.status.value in ("pending", "in-progress"))),
                z.slug,
            )
        console.print(table)


@app.command("show")
def show_zone(id_or_slug: str = typer.Argument(..., help="Zone ID or slug")):
    """Show zone details with spaces and open tasks."""
    from datetime import date
    with get_session() as session:
        try:
            zone = zone_svc.get_zone(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        content = {
            "ID": str(zone.id),
            "Slug": zone.slug,
            "Property": zone.property.name,
            "Description": zone.description,
            "Spaces": str(len(zone.spaces)),
        }
        console.print(format_panel(f"Zone: {zone.name}", content))

        if zone.spaces:
            space_table = Table(title="Spaces")
            space_table.add_column("ID", style="dim")
            space_table.add_column("Name", style="bold")
            space_table.add_column("Type")
            for s in sorted(zone.spaces, key=lambda s: s.name):
                space_table.add_row(str(s.id), s.name, s.space_type or "-")
            console.print(space_table)

        tasks = zone_svc.list_tasks_for_zone(session, id_or_slug)
        if tasks:
            today = date.today().isoformat()
            task_table = Table(title=f"Open Tasks ({len(tasks)})")
            task_table.add_column("ID", style="dim")
            task_table.add_column("Task", style="bold")
            task_table.add_column("Assignee")
            task_table.add_column("Priority")
            task_table.add_column("Due", style="dim")
            task_table.add_column("Recurrence")
            for t in tasks:
                due = str(t.due_date) if t.due_date else "-"
                due_display = f"[red]{due}[/red]" if t.due_date and due < today else due
                pri = t.priority.value if t.priority else "-"
                rec = t.schedule.frequency.value if t.schedule else "once"
                task_table.add_row(
                    str(t.id), t.title,
                    t.assignee.name if t.assignee else "-",
                    f"[{severity_color(pri)}]{pri}[/{severity_color(pri)}]",
                    due_display, rec,
                )
            console.print(task_table)
        else:
            console.print("[dim]No open tasks in this zone.[/dim]")


@app.command("edit")
def edit_zone(
    id_or_slug: str = typer.Argument(..., help="Zone ID or slug"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    description: Optional[str] = typer.Option(None, "--desc"),
):
    """Edit a zone."""
    kwargs = {}
    if name is not None: kwargs["name"] = name
    if description is not None: kwargs["description"] = description
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            zone = zone_svc.update_zone(session, id_or_slug, **kwargs)
            format_success(f"Zone '{zone.name}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("assign-space")
def assign_space(
    space: str = typer.Argument(..., help="Space ID or slug"),
    zone: str = typer.Option(..., "--zone", "-z", help="Zone ID or slug"),
):
    """Assign a space to a zone."""
    with get_session() as session:
        try:
            s = zone_svc.assign_space_to_zone(session, space, zone)
            format_success(f"Space '{s.name}' assigned to zone")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_zone(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a zone (spaces and tasks are unlinked, not deleted)."""
    with get_session() as session:
        try:
            zone = zone_svc.get_zone(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete zone '{zone.name}'? Spaces and tasks will be unlinked.", abort=True)
        name = zone_svc.delete_zone(session, id_or_slug)
        format_success(f"Zone '{name}' deleted")
