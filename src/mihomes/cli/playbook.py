"""Playbook CLI commands — read an account's playbooks (they are documents, not task templates)."""

import typer
from rich import box
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from mihomes.cli.formatters import console, format_error
from mihomes.db import get_session
from mihomes.services import playbook as pb_svc

app = typer.Typer(name="playbook", help="View operational playbooks")


def _load(slug: str) -> dict | None:
    """One playbook as a plain dict, read in its own session (playbooks are account rows)."""
    with get_session() as session:
        pb = pb_svc.get_playbook(session, slug)
        if pb is None:
            return None
        return {
            "slug": pb.slug, "name": pb.name, "type": pb.kind or "—", "owner": pb.audience or "—",
            "content": pb.content, "checklist": pb_svc.checklist(pb),
        }


@app.command("list")
def list_playbooks():
    """List all available playbooks."""
    with get_session() as session:
        playbooks = [
            {"slug": p.slug, "name": p.name, "type": p.kind or "—", "owner": p.audience or "—"}
            for p in pb_svc.list_playbooks(session)
        ]
    if not playbooks:
        console.print("[dim]No playbooks yet. Add one in the web app (Playbooks → New playbook "
                      "or Start from a template).[/dim]")
        return
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("Slug", style="cyan")
    t.add_column("Name", style="bold")
    t.add_column("Type", style="dim")
    t.add_column("Owner", style="dim")
    for pb in playbooks:
        t.add_row(pb["slug"], pb["name"], pb["type"], pb["owner"])
    console.print()
    console.print(t)
    console.print(f"[dim]  {len(playbooks)} playbook(s)[/dim]\n")


@app.command("show")
def show_playbook(
    slug: str = typer.Argument(..., help="Playbook slug"),
    raw: bool = typer.Option(False, "--raw", help="Show raw markdown instead of rendered"),
    checklist: bool = typer.Option(False, "--checklist", "-c", help="Show checklist items only"),
):
    """Show a playbook — rendered markdown or checklist view."""
    pb = _load(slug)
    if not pb:
        format_error(f"Playbook '{slug}' not found. Run: mihomes playbook list")
        raise typer.Exit(1)

    if checklist:
        items = pb["checklist"]
        if not items:
            console.print(f"[dim]No checklist items found in '{slug}'[/dim]")
            return
        console.print(f"\n[bold]{pb['name']}[/bold] — {len(items)} checklist items\n")
        current_section = None
        for item in items:
            if item["section"] != current_section:
                current_section = item["section"]
                console.print(f"  [dim bold]{current_section}[/dim bold]")
            console.print(f"    [dim]☐[/dim] {escape(item['title'])}")
        console.print()
        return

    if raw:
        console.print(pb["content"])
        return

    # Rendered markdown
    console.print()
    console.print(Panel(
        f"[bold]{pb['name']}[/bold]  ·  [dim]{pb['type']}[/dim]\n"
        f"[dim]Owner: {pb['owner']}[/dim]",
        box=box.ROUNDED,
    ))
    console.print(Markdown(pb["content"]))
