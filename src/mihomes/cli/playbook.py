"""Playbook CLI commands — markdown-first operational playbooks."""

from datetime import date, timedelta
from typing import Optional

import typer
from rich import box
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.models.task import TaskPriority
from mihomes.services import playbook as pb_svc
from mihomes.services.slug import EntityNotFoundError

app = typer.Typer(name="playbook", help="Manage and run operational playbooks")


@app.command("list")
def list_playbooks():
    """List all available playbooks."""
    playbooks = pb_svc.list_playbooks()
    if not playbooks:
        console.print(f"[dim]No playbooks found in knowledge/playbooks/[/dim]")
        console.print("[dim]Add .md files to create playbooks.[/dim]")
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
    console.print(f"[dim]  {len(playbooks)} playbook(s) · knowledge/playbooks/[/dim]\n")


@app.command("show")
def show_playbook(
    slug: str = typer.Argument(..., help="Playbook slug (filename without .md)"),
    raw: bool = typer.Option(False, "--raw", help="Show raw markdown instead of rendered"),
    checklist: bool = typer.Option(False, "--checklist", "-c", help="Show checklist items only"),
):
    """Show a playbook — rendered markdown or checklist view."""
    pb = pb_svc.get_playbook(slug)
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


@app.command("run")
def run_playbook(
    slug: str = typer.Argument(..., help="Playbook slug"),
    property: str = typer.Option(..., "--property", "-p", help="Property slug to create tasks for"),
    priority: str = typer.Option("medium", "--priority", help="Task priority: urgent/high/medium/low"),
    start: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD (default: today)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview tasks without creating them"),
):
    """
    Run a playbook — creates tasks from all checklist items for a property.

    Example:
        mihomes playbook run daily-operations --property miami
        mihomes playbook run onboarding-new-hire --property miami --start 2026-04-21
    """
    pb = pb_svc.get_playbook(slug)
    if not pb:
        format_error(f"Playbook '{slug}' not found. Run: mihomes playbook list")
        raise typer.Exit(1)

    if not pb["checklist"]:
        format_error(f"Playbook '{slug}' has no checklist items — nothing to run.")
        raise typer.Exit(1)

    try:
        pri = TaskPriority(priority.lower())
    except ValueError:
        format_error(f"Invalid priority '{priority}'. Use: urgent, high, medium, low")
        raise typer.Exit(1)

    start_date = date.fromisoformat(start) if start else date.today()

    if dry_run:
        console.print(f"\n[dim]Dry run — no tasks will be created[/dim]")
        console.print(f"[bold]{pb['name']}[/bold] → {escape(property)} (from {start_date})\n")
        t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        t.add_column("#", style="dim", justify="right")
        t.add_column("Task")
        t.add_column("Section", style="dim")
        t.add_column("Due", style="dim", justify="right")
        for i, item in enumerate(pb["checklist"], 1):
            due = (start_date + timedelta(days=item.get("day_offset", 0))).isoformat()
            t.add_row(str(i), item["title"], item["section"], due)
        console.print(t)
        console.print(f"\n[dim]{len(pb['checklist'])} tasks would be created.[/dim]\n")
        return

    with get_session() as session:
        try:
            tasks = pb_svc.run_playbook(
                session, slug, property,
                priority=pri, start_date=start_date,
            )
        except (ValueError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

    format_success(f"Created {len(tasks)} tasks from '{pb['name']}' for {property}")
    console.print(f"[dim]  Run 'mihomes task list --property {escape(property)}' to see them.[/dim]")


@app.command("search")
def search_knowledge(
    query: str = typer.Argument(..., help="Search term"),
):
    """Search across all files in the knowledge base."""
    results = pb_svc.knowledge_search(query)
    if not results:
        console.print(f"[dim]No matches for '{query}' in knowledge base.[/dim]")
        return

    console.print(f"\n[bold]Knowledge base search: '{escape(query)}'[/bold] — {len(results)} file(s)\n")
    for r in results:
        console.print(f"  [cyan]{r['file']}[/cyan]  [dim]{r['title']}[/dim]")
        for lineno, line in r["matches"]:
            console.print(f"    [dim]{lineno:>4}:[/dim] {escape(line[:100])}")
        console.print()
