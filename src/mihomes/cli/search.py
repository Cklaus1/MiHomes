"""Search CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, esc
from mihomes.db import get_session
from mihomes.services.search import global_search

app = typer.Typer(name="search", help="Search across all entities", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    entity_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by entity type"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filter by tag"),
):
    """Search across all entities."""
    if ctx.invoked_subcommand is not None:
        return

    with get_session() as session:
        if tag:
            from mihomes.services.tag import get_tagged_entities
            results = get_tagged_entities(session, tag)
            if not results:
                console.print(f"[dim]No entities tagged '{esc(tag)}'.[/dim]")
                return
            table = Table(title=f"Tagged: {esc(tag)}")
            table.add_column("Type")
            table.add_column("ID", style="dim")
            table.add_column("Name", style="bold")
            for r in results:
                table.add_row(r["type"], str(r["id"]), esc(r["name"]))
            console.print(table)
            return

        results = global_search(session, query, entity_type=entity_type)
        if not results:
            console.print(f"[dim]No results for '{esc(query)}'.[/dim]")
            return
        table = Table(title=f"Search: {esc(query)}")
        table.add_column("Type")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Slug", style="dim")
        for r in results:
            table.add_row(r["type"], str(r["id"]), esc(r["name"]), r.get("slug") or "-")
        console.print(table)
