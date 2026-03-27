"""Note CLI commands."""

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.services import note as note_svc

app = typer.Typer(name="note", help="Manage notes on any entity")


@app.command("add")
def add_note(
    content: str = typer.Argument(..., help="Note content"),
    to: str = typer.Option(..., "--to", help="Entity reference (e.g., property:beach-house)"),
):
    """Add a note to an entity."""
    with get_session() as session:
        try:
            note = note_svc.add_note(session, to, content)
            format_success(f"Note added to {to} (id: {note.id})")
        except (ValueError, Exception) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_notes(
    to: str = typer.Option(..., "--to", help="Entity reference (e.g., property:beach-house)"),
):
    """List notes for an entity."""
    with get_session() as session:
        try:
            notes = note_svc.list_notes(session, to)
        except (ValueError, Exception) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not notes:
            console.print("[dim]No notes found.[/dim]")
            return

        table = Table(title=f"Notes for {to}")
        table.add_column("ID", style="dim")
        table.add_column("Content")
        table.add_column("Created", style="dim")
        for n in notes:
            table.add_row(str(n.id), n.content, str(n.created_at)[:19])
        console.print(table)


@app.command("delete")
def delete_note(
    note_id: int = typer.Argument(..., help="Note ID"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a note."""
    if not force:
        typer.confirm(f"Delete note {note_id}?", abort=True)
    with get_session() as session:
        try:
            note_svc.delete_note(session, note_id)
            format_success(f"Note {note_id} deleted")
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)
