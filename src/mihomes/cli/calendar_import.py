"""Calendar import CLI — import iCal files into occupancy events."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session

app = typer.Typer(name="calendar", help="Calendar import and occupancy management")


@app.command("import")
def import_ical(
    file: str = typer.Argument(..., help="Path to .ics file"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Associate events with property"),
):
    """Import events from an iCal (.ics) file."""
    from mihomes.services.gateways.calendar.ical import parse_ical_file

    try:
        events = parse_ical_file(file)
    except FileNotFoundError as e:
        format_error(str(e))
        raise typer.Exit(1)

    if not events:
        console.print("[dim]No events found in file.[/dim]")
        return

    table = Table(title=f"Parsed Events ({len(events)})")
    table.add_column("#", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Start")
    table.add_column("End")
    table.add_column("Location")
    for i, ev in enumerate(events, 1):
        table.add_row(
            str(i), ev.get("title", "-"),
            str(ev.get("start", "-")), str(ev.get("end", "-")),
            ev.get("location", "-"),
        )
    console.print(table)

    if not typer.confirm(f"\nImport {len(events)} event(s)?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    with get_session() as session:
        if property:
            from mihomes.services.slug import resolve_identifier, EntityNotFoundError
            from mihomes.models.property import Property
            try:
                prop = resolve_identifier(session, Property, property)
            except EntityNotFoundError as e:
                format_error(str(e))
                raise typer.Exit(1)

        from mihomes.services.property import occupy_property
        imported = 0
        for ev in events:
            start = ev.get("start")
            end = ev.get("end")
            if property and start:
                try:
                    from datetime import date as date_type
                    start_date = start if isinstance(start, date_type) else start.date() if hasattr(start, "date") else None
                    end_date = end if isinstance(end, date_type) else end.date() if hasattr(end, "date") else None
                    if start_date:
                        occupy_property(session, property, start_date, end_date)
                        imported += 1
                except Exception:
                    pass  # Skip events that can't be parsed as occupancy

        format_success(f"{imported} event(s) imported as occupancy periods")


@app.command("list")
def list_occupancy(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
):
    """Show occupancy periods for properties."""
    from mihomes.services.property import list_properties

    with get_session() as session:
        props = list_properties(session)
        table = Table(title="Property Occupancy")
        table.add_column("Property", style="bold")
        table.add_column("Occupied")
        table.add_column("Since")
        table.add_column("Until")
        for p in props:
            if property and p.slug != property:
                continue
            table.add_row(
                p.name,
                "[green]Yes[/green]" if p.occupied else "No",
                str(p.occupied_since) if p.occupied_since else "-",
                str(p.occupied_until) if p.occupied_until else "-",
            )
        console.print(table)
