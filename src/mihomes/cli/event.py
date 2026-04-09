"""Event CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_panel, format_success, status_icon
from mihomes.db import get_session
from mihomes.models.event import EventStatus
from mihomes.services import event as event_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="event", help="Manage events and hospitality")


@app.command("add")
def add_event(
    title: str = typer.Argument(..., help="Event title"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    event_date: str = typer.Option(..., "--date", help="Event date YYYY-MM-DD"),
    end_date: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
    expected_guests: Optional[int] = typer.Option(None, "--guests", help="Expected number of guests"),
    budget: Optional[float] = typer.Option(None, "--budget"),
    description: Optional[str] = typer.Option(None, "--desc"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Add a new event."""
    from datetime import date
    with get_session() as session:
        try:
            event = event_svc.create_event(
                session, title, property,
                date.fromisoformat(event_date),
                end_date=date.fromisoformat(end_date) if end_date else None,
                expected_guests=expected_guests, budget=budget,
                description=description, notes=notes,
            )
            format_success(f"Event '{event.title}' created (slug: {event.slug}, date: {event.event_date})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_events(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    status: Optional[EventStatus] = typer.Option(None, "--status", "-s"),
):
    """List events."""
    with get_session() as session:
        try:
            events = event_svc.list_events(session, property_id_or_slug=property, status=status)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not events:
            console.print("[dim]No events found.[/dim]")
            return

        table = Table(title="Events")
        table.add_column("ID", style="dim")
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Date")
        table.add_column("Guests")
        table.add_column("Status")
        table.add_column("Budget", justify="right")
        table.add_column("Slug", style="dim")
        for e in events:
            table.add_row(
                str(e.id), e.title, e.property.name,
                str(e.event_date),
                str(e.expected_guests) if e.expected_guests else "-",
                f"{status_icon(e.status)} {format_enum(e.status)}",
                f"{e.currency} {e.budget:,.0f}" if e.budget else "-",
                e.slug,
            )
        console.print(table)


@app.command("edit")
def edit_event(
    id_or_slug: str = typer.Argument(..., help="Event ID or slug"),
    title: Optional[str] = typer.Option(None, "--title"),
    event_date: Optional[str] = typer.Option(None, "--date", help="Event date YYYY-MM-DD"),
    end_date: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
    expected_guests: Optional[int] = typer.Option(None, "--guests"),
    budget: Optional[float] = typer.Option(None, "--budget"),
    status: Optional[EventStatus] = typer.Option(None, "--status", "-s"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Edit an event."""
    from datetime import date
    kwargs = {}
    if title is not None: kwargs["title"] = title
    if event_date is not None: kwargs["event_date"] = date.fromisoformat(event_date)
    if end_date is not None: kwargs["end_date"] = date.fromisoformat(end_date)
    if expected_guests is not None: kwargs["expected_guests"] = expected_guests
    if budget is not None: kwargs["budget"] = budget
    if status is not None: kwargs["status"] = status
    if notes is not None: kwargs["notes"] = notes
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            event = event_svc.update_event(session, id_or_slug, **kwargs)
            format_success(f"Event '{event.title}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("show")
def show_event(id_or_slug: str = typer.Argument(...)):
    """Show event details."""
    with get_session() as session:
        try:
            event = event_svc.get_event(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(event.id),
            "Slug": event.slug,
            "Property": event.property.name,
            "Date": str(event.event_date),
            "End Date": str(event.end_date) if event.end_date else None,
            "Expected Guests": str(event.expected_guests) if event.expected_guests else None,
            "Budget": f"{event.currency} {event.budget:,.2f}" if event.budget else None,
            "Status": f"{status_icon(event.status)} {format_enum(event.status)}",
            "Description": event.description,
            "Notes": event.notes,
        }
        console.print(format_panel(f"Event: {event.title}", content))
        # Show guest list
        if event.guests:
            guest_table = Table(title="Guest List")
            guest_table.add_column("Guest")
            guest_table.add_column("RSVP")
            guest_table.add_column("Notes")
            for eg in event.guests:
                guest_table.add_row(eg.guest.name, eg.rsvp_status, eg.notes or "-")
            console.print(guest_table)
