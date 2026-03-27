"""Task CLI commands."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import (
    console,
    format_enum,
    format_error,
    format_panel,
    format_success,
    severity_color,
    status_icon,
)
from mihomes.db import get_session
from mihomes.models.task import RecurrenceFrequency, TaskPriority, TaskStatus
from mihomes.services import task as task_svc
from mihomes.services.slug import EntityNotFoundError

app = typer.Typer(name="task", help="Manage tasks")


@app.command("add")
def add_task(
    title: str = typer.Argument(..., help="Task title"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    priority: TaskPriority = typer.Option(TaskPriority.MEDIUM, "--priority", help="Priority level"),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Staff ID or slug"),
    due: Optional[str] = typer.Option(None, "--due", help="Due date (YYYY-MM-DD)"),
    recurrence: RecurrenceFrequency = typer.Option(RecurrenceFrequency.ONCE, "--recurrence", "-r", help="Recurrence"),
    season: Optional[str] = typer.Option(None, "--season", help="Season spec for seasonal tasks (e.g., spring,fall)"),
    description: Optional[str] = typer.Option(None, "--desc", help="Description"),
):
    """Add a new task."""
    with get_session() as session:
        try:
            due_date = date.fromisoformat(due) if due else None
            task = task_svc.create_task(
                session, title, property,
                description=description, priority=priority,
                assignee_id_or_slug=assignee, due_date=due_date,
                recurrence=recurrence, season_spec=season,
            )
            msg = f"Task '{task.title}' created (slug: {task.slug})"
            if task.schedule:
                msg += f" [recurrence: {task.schedule.frequency.value}]"
            format_success(msg)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_tasks(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    status: Optional[TaskStatus] = typer.Option(None, "--status"),
    priority: Optional[TaskPriority] = typer.Option(None, "--priority"),
    assignee: Optional[str] = typer.Option(None, "--assignee"),
    overdue: bool = typer.Option(False, "--overdue", help="Show only overdue tasks"),
):
    """List tasks."""
    with get_session() as session:
        try:
            tasks = task_svc.list_tasks(
                session, property_id_or_slug=property, status=status,
                priority=priority, assignee_id_or_slug=assignee, overdue=overdue,
            )
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not tasks:
            console.print("[dim]No tasks found.[/dim]")
            return

        table = Table(title="Tasks")
        table.add_column("ID", style="dim")
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Priority")
        table.add_column("Status")
        table.add_column("Due")
        table.add_column("Recurrence")
        for t in tasks:
            prio_style = severity_color(t.priority.value)
            rec = t.schedule.frequency.value if t.schedule else "once"
            due_str = str(t.due_date) if t.due_date else "-"
            if t.due_date and t.due_date < date.today() and t.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                due_str = f"[red]{due_str} (overdue)[/red]"
            table.add_row(
                str(t.id), t.title, t.property.name,
                f"[{prio_style}]{format_enum(t.priority)}[/{prio_style}]",
                f"{status_icon(t.status)} {format_enum(t.status)}",
                due_str, rec,
            )
        console.print(table)


@app.command("show")
def show_task(id_or_slug: str = typer.Argument(...)):
    """Show task details."""
    with get_session() as session:
        try:
            task = task_svc.get_task(session, id_or_slug)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(task.id),
            "Slug": task.slug,
            "Property": task.property.name,
            "Priority": format_enum(task.priority),
            "Status": f"{status_icon(task.status)} {format_enum(task.status)}",
            "Due Date": str(task.due_date) if task.due_date else None,
            "Assignee": task.assignee.name if task.assignee else None,
            "Description": task.description,
            "Completed At": str(task.completed_at) if task.completed_at else None,
            "Completion Notes": task.completion_notes,
        }
        console.print(format_panel(f"Task: {task.title}", content))
        if task.schedule:
            sched = {
                "Frequency": task.schedule.frequency.value,
                "Season Spec": task.schedule.season_spec,
                "Next Due": str(task.schedule.next_due) if task.schedule.next_due else None,
            }
            console.print(format_panel("Schedule", sched))


@app.command("complete")
def complete_task(
    id_or_slug: str = typer.Argument(...),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Completion notes"),
):
    """Mark a task as completed."""
    with get_session() as session:
        try:
            task = task_svc.complete_task(session, id_or_slug, notes=notes)
            format_success(f"Task '{task.title}' completed")
            if task.schedule and task.schedule.frequency != RecurrenceFrequency.ONCE:
                console.print("[dim]Next occurrence has been created.[/dim]")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("upcoming")
def upcoming_tasks(
    days: int = typer.Option(14, "--days", "-d", help="Number of days to look ahead"),
    property: Optional[str] = typer.Option(None, "--property", "-p"),
):
    """Show tasks due in the next N days."""
    with get_session() as session:
        try:
            tasks = task_svc.get_upcoming_tasks(session, days=days, property_id_or_slug=property)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not tasks:
            console.print(f"[dim]No tasks due in the next {days} days.[/dim]")
            return

        table = Table(title=f"Tasks Due in Next {days} Days")
        table.add_column("Due", style="bold")
        table.add_column("Title")
        table.add_column("Property")
        table.add_column("Priority")
        for t in tasks:
            prio_style = severity_color(t.priority.value)
            table.add_row(
                str(t.due_date), t.title, t.property.name,
                f"[{prio_style}]{format_enum(t.priority)}[/{prio_style}]",
            )
        console.print(table)


@app.command("edit")
def edit_task(
    id_or_slug: str = typer.Argument(...),
    title: Optional[str] = typer.Option(None, "--title"),
    priority: Optional[TaskPriority] = typer.Option(None, "--priority"),
    assignee: Optional[str] = typer.Option(None, "--assignee"),
    due: Optional[str] = typer.Option(None, "--due"),
    status: Optional[TaskStatus] = typer.Option(None, "--status"),
):
    """Edit a task."""
    kwargs = {}
    if title is not None: kwargs["title"] = title
    if priority is not None: kwargs["priority"] = priority
    if due is not None: kwargs["due_date"] = date.fromisoformat(due)
    if status is not None: kwargs["status"] = status
    if assignee is not None:
        with get_session() as session:
            from mihomes.models.staff import Staff
            from mihomes.services.slug import resolve_identifier
            staff = resolve_identifier(session, Staff, assignee)
            kwargs["assignee_id"] = staff.id
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            task = task_svc.update_task(session, id_or_slug, **kwargs)
            format_success(f"Task '{task.title}' updated")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_task(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a task."""
    with get_session() as session:
        try:
            task = task_svc.get_task(session, id_or_slug)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete task '{task.title}'?", abort=True)
        name = task_svc.delete_task(session, id_or_slug)
        format_success(f"Task '{name}' deleted")
