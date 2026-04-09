"""Guest CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.services import event as event_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="guest", help="Manage guests")


@app.command("add")
def add_guest(
    name: str = typer.Argument(..., help="Guest name"),
    email: Optional[str] = typer.Option(None, "--email"),
    phone: Optional[str] = typer.Option(None, "--phone"),
    dietary: Optional[str] = typer.Option(None, "--dietary", help="Dietary preferences"),
    room: Optional[str] = typer.Option(None, "--room", help="Room preference"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Add a new guest."""
    with get_session() as session:
        guest = event_svc.create_guest(
            session, name, email=email, phone=phone,
            dietary_preferences=dietary, room_preference=room, notes=notes,
        )
        format_success(f"Guest '{guest.name}' added (slug: {guest.slug})")


@app.command("list")
def list_guests():
    """List all guests."""
    with get_session() as session:
        guests = event_svc.list_guests(session)
        if not guests:
            console.print("[dim]No guests found.[/dim]")
            return
        table = Table(title="Guests")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Email")
        table.add_column("Phone")
        table.add_column("Dietary")
        table.add_column("Slug", style="dim")
        for g in guests:
            table.add_row(
                str(g.id), g.name, g.email or "-", g.phone or "-",
                g.dietary_preferences or "-", g.slug,
            )
        console.print(table)


@app.command("invite")
def invite_guest(
    guest: str = typer.Argument(..., help="Guest ID or slug"),
    to: str = typer.Option(..., "--to", help="Event reference e.g. event:slug"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Invite a guest to an event."""
    # Parse the --to reference
    parts = to.split(":")
    if len(parts) == 2 and parts[0] == "event":
        event_ref = parts[1]
    else:
        # Assume it's a direct event slug/id
        event_ref = to
    with get_session() as session:
        try:
            eg = event_svc.invite_guest(session, event_ref, guest, notes=notes)
            format_success(f"Guest '{eg.guest.name}' invited to event '{eg.event.title}' (RSVP: {eg.rsvp_status})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("rsvp")
def update_rsvp(
    guest: str = typer.Argument(..., help="Guest ID or slug"),
    event: str = typer.Option(..., "--event", "-e", help="Event ID or slug"),
    status: str = typer.Option(..., "--status", "-s", help="RSVP status (accepted, declined, tentative)"),
):
    """Update RSVP status for a guest."""
    with get_session() as session:
        try:
            eg = event_svc.update_rsvp(session, event, guest, status)
            format_success(f"RSVP for '{eg.guest.name}' updated to '{eg.rsvp_status}'")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)
