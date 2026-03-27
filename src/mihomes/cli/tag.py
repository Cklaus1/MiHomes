"""Tag CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.services import tag as tag_svc

app = typer.Typer(name="tag", help="Manage tags and labels")


@app.command("create")
def create_tag(name: str = typer.Argument(..., help="Tag name")):
    """Create a tag."""
    with get_session() as session:
        tag = tag_svc.create_tag(session, name)
        format_success(f"Tag '{tag.name}' created")


@app.command("list")
def list_tags():
    """List all tags."""
    with get_session() as session:
        tags = tag_svc.list_tags(session)
        if not tags:
            console.print("[dim]No tags found.[/dim]")
            return
        for t in tags:
            console.print(f"  [bold]{t.name}[/bold]")


@app.command("apply")
def apply_tag(
    tag_name: str = typer.Argument(..., help="Tag name"),
    to: list[str] = typer.Option(..., "--to", help="Entity refs (e.g., issue:42 task:87)"),
):
    """Apply a tag to one or more entities."""
    with get_session() as session:
        try:
            count = tag_svc.apply_tag(session, tag_name, to)
            format_success(f"Tag '{tag_name}' applied to {count} entity(ies)")
        except Exception as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("show")
def show_tag(tag_name: str = typer.Argument(..., help="Tag name")):
    """Show all entities with a tag."""
    with get_session() as session:
        entities = tag_svc.get_tagged_entities(session, tag_name)
        if not entities:
            console.print(f"[dim]No entities tagged '{tag_name}'.[/dim]")
            return
        table = Table(title=f"Tag: {tag_name}")
        table.add_column("Type")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        for e in entities:
            table.add_row(e["type"], str(e["id"]), e["name"])
        console.print(table)
