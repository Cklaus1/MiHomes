"""Template CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.models.task import TaskPriority
from mihomes.services import template as tmpl_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="template", help="Manage reusable task templates")


@app.command("add")
def add_template(
    name: str = typer.Argument(..., help="Template name"),
    steps: Optional[str] = typer.Option(None, "--steps", help="Comma-separated step titles"),
    description: Optional[str] = typer.Option(None, "--desc"),
):
    """Create a template."""
    step_list = [s.strip() for s in steps.split(",")] if steps else None
    with get_session() as session:
        tmpl = tmpl_svc.create_template(session, name, description=description, steps=step_list)
        step_count = len(tmpl.items) if tmpl.items else 0
        format_success(f"Template '{tmpl.name}' created with {step_count} steps (slug: {tmpl.slug})")


@app.command("list")
def list_templates():
    """List all templates."""
    with get_session() as session:
        templates = tmpl_svc.list_templates(session)
        if not templates:
            console.print("[dim]No templates found.[/dim]")
            return
        table = Table(title="Templates")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Steps")
        table.add_column("Slug", style="dim")
        for t in templates:
            table.add_row(str(t.id), t.name, str(len(t.items)), t.slug)
        console.print(table)


@app.command("show")
def show_template(id_or_slug: str = typer.Argument(...)):
    """Show template details and steps."""
    with get_session() as session:
        try:
            tmpl = tmpl_svc.get_template(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        console.print(f"\n[bold]{tmpl.name}[/bold]")
        if tmpl.description:
            console.print(f"[dim]{tmpl.description}[/dim]")
        console.print()
        for i, item in enumerate(tmpl.items, 1):
            console.print(f"  {i}. {item.title}")
        console.print()


@app.command("run")
def run_template(
    id_or_slug: str = typer.Argument(..., help="Template ID or slug"),
    property: str = typer.Option(..., "--property", "-p"),
    due: Optional[str] = typer.Option(None, "--due", help="Base due date YYYY-MM-DD"),
    priority: TaskPriority = typer.Option(TaskPriority.MEDIUM, "--priority"),
):
    """Instantiate a template into tasks for a property."""
    with get_session() as session:
        try:
            due_date = date.fromisoformat(due) if due else None
            tasks = tmpl_svc.run_template(session, id_or_slug, property, due_date=due_date, priority=priority)
            format_success(f"{len(tasks)} tasks created from template")
            for t in tasks:
                console.print(f"  - {t.title} (due: {t.due_date})")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("seed")
def seed_templates():
    """Seed built-in seasonal maintenance templates."""
    from mihomes.services.seasonal import seed_templates as do_seed
    with get_session() as session:
        created = do_seed(session)
        # Collect output data before session closes (commit happens on exit)
        results = [(t.name, t.slug, len(t.items)) for t in created] if created else []
    # Print AFTER session is committed (safe from SIGPIPE data loss)
    if results:
        format_success(f"Seeded {len(results)} seasonal templates:")
        for name, slug, steps in results:
            console.print(f"  - {name} (slug: {slug}, {steps} steps)")
    else:
        console.print("[dim]All seasonal templates already exist.[/dim]")


@app.command("delete")
def delete_template(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a template."""
    with get_session() as session:
        try:
            tmpl = tmpl_svc.get_template(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete template '{tmpl.name}'?", abort=True)
        name = tmpl_svc.delete_template(session, id_or_slug)
        format_success(f"Template '{name}' deleted")
