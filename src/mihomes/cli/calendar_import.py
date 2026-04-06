"""Calendar CLI — Google Calendar sync, iCal import, and occupancy management."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session

app = typer.Typer(name="calendar", help="Calendar sync and occupancy management")


@app.command("auth")
def google_auth():
    """Authenticate with Google Calendar (opens browser for OAuth)."""
    from pathlib import Path
    creds_file = Path.home() / ".mihomes" / "google_credentials.json"
    if not creds_file.exists():
        console.print(f"[yellow]Credentials file not found at:[/yellow] {creds_file}\n")
        console.print("To set up Google Calendar:\n"
                      "  1. Go to [link]https://console.cloud.google.com[/link]\n"
                      "  2. Create a project → APIs & Services → Enable [bold]Google Calendar API[/bold]\n"
                      "  3. Credentials → Create → OAuth 2.0 Client ID → Desktop app\n"
                      "  4. Download JSON → save as [bold]~/.mihomes/google_credentials.json[/bold]\n"
                      "  5. Run [bold]mihomes calendar auth[/bold] again")
        raise typer.Exit(1)
    try:
        from mihomes.services.gateways.calendar.google import GoogleCalendarProvider
        provider = GoogleCalendarProvider()
        _ = provider.service  # Triggers OAuth flow
        format_success("Google Calendar authenticated successfully")
        console.print("[dim]Token saved to ~/.mihomes/google_token.json[/dim]")
    except Exception as e:
        format_error(f"Authentication failed: {e}")
        raise typer.Exit(1)


@app.command("pull")
def google_pull(
    days: int = typer.Option(30, "--days", "-d", help="Number of days ahead to pull"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Associate events with property"),
):
    """Pull events from Google Calendar into MiHomes occupancy."""
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    token_file = Path.home() / ".mihomes" / "google_token.json"
    if not token_file.exists():
        format_error("Not authenticated. Run: mihomes calendar auth")
        raise typer.Exit(1)

    try:
        from mihomes.services.gateways.calendar.google import GoogleCalendarProvider
        provider = GoogleCalendarProvider()
        now = datetime.now(timezone.utc)
        events = provider.list_events(now, now + timedelta(days=days))
    except Exception as e:
        format_error(f"Failed to fetch Google Calendar events: {e}")
        raise typer.Exit(1)

    if not events:
        console.print(f"[dim]No events found in the next {days} days.[/dim]")
        return

    table = Table(title=f"Google Calendar — Next {days} Days ({len(events)} events)")
    table.add_column("Title", style="bold")
    table.add_column("Start")
    table.add_column("End")
    for ev in events:
        table.add_row(ev["title"], str(ev["start"])[:16], str(ev["end"])[:16])
    console.print(table)

    if property:
        if not typer.confirm(f"\nImport as occupancy periods for {property}?"):
            console.print("[dim]Cancelled.[/dim]")
            return
        with get_session() as session:
            from mihomes.services.property import occupy_property
            imported = 0
            for ev in events:
                try:
                    start_date = ev["start"].date() if hasattr(ev["start"], "date") else ev["start"]
                    end_date = ev["end"].date() if hasattr(ev["end"], "date") else ev["end"]
                    occupy_property(session, property, start_date, end_date)
                    imported += 1
                except Exception:
                    pass
            format_success(f"{imported} event(s) imported as occupancy for {property}")


@app.command("push")
def google_push(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Push tasks/events for this property"),
    days: int = typer.Option(14, "--days", "-d", help="Push items due in next N days"),
):
    """Push upcoming MiHomes tasks and events to Google Calendar."""
    from datetime import datetime, timezone, timedelta, date
    from pathlib import Path

    token_file = Path.home() / ".mihomes" / "google_token.json"
    if not token_file.exists():
        format_error("Not authenticated. Run: mihomes calendar auth")
        raise typer.Exit(1)

    with get_session() as session:
        from mihomes.services.task import get_upcoming_tasks as list_upcoming_tasks
        from mihomes.services.event import list_events as list_mihomes_events

        tasks = list_upcoming_tasks(session, days=days,
                                    property_id_or_slug=property)

        items = []
        for t in tasks:
            if t.due_date:
                items.append({
                    "title": f"[MiHomes] {t.title}",
                    "start": t.due_date,
                    "end": t.due_date,
                    "description": t.description or "",
                })

    if not items:
        console.print(f"[dim]No upcoming tasks with due dates in the next {days} days.[/dim]")
        return

    console.print(f"[bold]{len(items)} item(s) to push to Google Calendar:[/bold]")
    for item in items:
        console.print(f"  • {item['title']} — {item['start']}")

    if not typer.confirm("\nPush to Google Calendar?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    try:
        from mihomes.services.gateways.calendar.google import GoogleCalendarProvider
        provider = GoogleCalendarProvider()
        results = provider.sync_from_mihomes(items)
    except Exception as e:
        format_error(f"Failed to push to Google Calendar: {e}")
        raise typer.Exit(1)

    created = sum(1 for r in results if r["status"] == "created")
    errors = [r for r in results if r["status"] == "error"]
    format_success(f"{created} item(s) pushed to Google Calendar")
    for err in errors:
        console.print(f"  [yellow]⚠[/yellow] {err['title']}: {err['error']}")


@app.command("sync")
def google_sync(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    days: int = typer.Option(30, "--days", "-d"),
):
    """Two-way sync — pull from Google Calendar and push upcoming tasks."""
    console.print("[bold]Syncing with Google Calendar...[/bold]\n")
    console.print("[dim]Step 1/2 — Pulling events from Google Calendar[/dim]")
    ctx = typer.Context(google_sync)
    google_pull.callback(days=days, property=property)
    console.print("\n[dim]Step 2/2 — Pushing upcoming tasks to Google Calendar[/dim]")
    google_push.callback(property=property, days=days)


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
                except (ValueError, TypeError, KeyError):
                    console.print(f"[dim]  Skipped: {ev.get('title', 'unknown')} (parse error)[/dim]")

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
