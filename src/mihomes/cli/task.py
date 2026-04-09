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
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="task", help="Manage tasks")


def _fmt_hours(h: float | None) -> str:
    """Format hours as '2h', '30m', or '1h 30m'."""
    if h is None:
        return "-"
    total_mins = round(h * 60)
    hrs, mins = divmod(total_mins, 60)
    if hrs and mins:
        return f"{hrs}h {mins}m"
    if hrs:
        return f"{hrs}h"
    return f"{mins}m"


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
    estimate: Optional[float] = typer.Option(None, "--estimate", "-e", help="Estimated hours to complete (e.g. 1.5)"),
    ai_suggest: bool = typer.Option(False, "--ai-suggest", "-a", help="Get AI suggestions for priority and tags"),
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
                estimated_hours=estimate,
            )
            msg = f"Task '{task.title}' created (slug: {task.slug})"
            if task.schedule:
                msg += f" [recurrence: {task.schedule.frequency.value}]"
            format_success(msg)

            if ai_suggest:
                _apply_ai_suggestions(session, "task", task.slug, title, description, task.property.name)

        except (EntityNotFoundError, ValueError) as e:
            format_error(str(e))
            raise typer.Exit(1)


def _apply_ai_suggestions(session, entity_type: str, slug: str, title: str, description, property_name: str) -> None:
    """Fetch AI suggestions and interactively apply priority/severity + tags."""
    from mihomes.services.ai.assessors import suggest_tags_and_priority
    from mihomes.services.ai.provider import AIAuthError, AIProviderError
    from mihomes.services.tag import apply_tag

    try:
        with console.status("[bold blue]Getting AI suggestions...", spinner="dots"):
            suggestions = suggest_tags_and_priority(session, entity_type, title, description, property_name)
    except (AIAuthError, AIProviderError) as e:
        console.print(f"[yellow]AI suggestions unavailable: {e}[/yellow]")
        return

    if not suggestions:
        return

    console.print()
    console.print("[bold]AI Suggestions[/bold]")
    if suggestions.get("reasoning"):
        console.print(f"[dim]{suggestions['reasoning']}[/dim]")

    priority_key = "priority" if entity_type == "task" else "severity"
    suggested_priority = suggestions.get(priority_key)
    suggested_tags = suggestions.get("tags", [])

    changes_made = []

    if suggested_priority:
        console.print(f"  {priority_key.capitalize()}: [bold]{suggested_priority}[/bold]")
        if typer.confirm(f"  Apply {priority_key}?", default=True):
            if entity_type == "task":
                from mihomes.services.task import update_task
                from mihomes.models.task import TaskPriority
                update_task(session, slug, priority=TaskPriority(suggested_priority))
            else:
                from mihomes.services.issue import update_issue
                from mihomes.models.issue import IssueSeverity
                update_issue(session, slug, severity=IssueSeverity(suggested_priority))
            changes_made.append(f"{priority_key}={suggested_priority}")

    if suggested_tags:
        console.print(f"  Tags: [bold]{', '.join(suggested_tags)}[/bold]")
        if typer.confirm("  Apply tags?", default=True):
            for tag in suggested_tags:
                apply_tag(session, tag, [f"{entity_type}:{slug}"])
            changes_made.append(f"tags: {', '.join(suggested_tags)}")

    if changes_made:
        format_success(f"Applied: {', '.join(changes_made)}")


@app.command("list")
def list_tasks(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    status: Optional[TaskStatus] = typer.Option(None, "--status"),
    priority: Optional[TaskPriority] = typer.Option(None, "--priority"),
    assignee: Optional[str] = typer.Option(None, "--assignee"),
    overdue: bool = typer.Option(False, "--overdue", help="Show only overdue tasks"),
    recent: bool = typer.Option(False, "--recent", help="Show recently completed tasks (last 7 days)"),
):
    """List tasks."""
    with get_session() as session:
        try:
            if recent:
                from datetime import datetime, timedelta, timezone
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                from mihomes.models.task import Task as TaskModel
                tasks = session.query(TaskModel).filter(
                    TaskModel.status == TaskStatus.COMPLETED,
                    TaskModel.completed_at >= cutoff,
                ).order_by(TaskModel.completed_at.desc()).all()
            else:
                tasks = task_svc.list_tasks(
                    session, property_id_or_slug=property, status=status,
                    priority=priority, assignee_id_or_slug=assignee, overdue=overdue,
                )
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
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
        table.add_column("Est.")
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
                _fmt_hours(t.estimated_hours),
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
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(task.id),
            "Slug": task.slug,
            "Property": task.property.name,
            "Priority": format_enum(task.priority),
            "Estimated Time": _fmt_hours(task.estimated_hours),
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
        except (EntityNotFoundError, ValueError) as e:
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
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
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
        table.add_column("Est.")
        for t in tasks:
            prio_style = severity_color(t.priority.value)
            table.add_row(
                str(t.due_date), t.title, t.property.name,
                f"[{prio_style}]{format_enum(t.priority)}[/{prio_style}]",
                _fmt_hours(t.estimated_hours),
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
    estimate: Optional[float] = typer.Option(None, "--estimate", "-e", help="Estimated hours (e.g. 1.5)"),
):
    """Edit a task."""
    kwargs = {}
    if title is not None: kwargs["title"] = title
    if priority is not None: kwargs["priority"] = priority
    if due is not None: kwargs["due_date"] = date.fromisoformat(due)
    if status is not None: kwargs["status"] = status
    if estimate is not None: kwargs["estimated_hours"] = estimate
    # Assignee is resolved inside the same session below
    if not kwargs and assignee is None:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        if assignee is not None:
            from mihomes.models.staff import Staff
            from mihomes.services.slug import resolve_identifier as resolve_id
            staff = resolve_id(session, Staff, assignee)
            kwargs["assignee_id"] = staff.id
        try:
            task = task_svc.update_task(session, id_or_slug, **kwargs)
            format_success(f"Task '{task.title}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("by-frequency")
def tasks_by_frequency(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Filter by property slug"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="Filter by staff slug or name"),
    frequency: Optional[RecurrenceFrequency] = typer.Option(None, "--frequency", "-f", help="Show only one frequency"),
):
    """Show all recurring tasks grouped by frequency (daily, weekly, monthly, etc.)."""
    from mihomes.models.task import Task, TaskSchedule, TaskStatus
    from mihomes.models.staff import Staff
    from mihomes.models.property import Property

    FREQUENCY_ORDER = [
        RecurrenceFrequency.DAILY,
        RecurrenceFrequency.WEEKLY,
        RecurrenceFrequency.BIWEEKLY,
        RecurrenceFrequency.MONTHLY,
        RecurrenceFrequency.QUARTERLY,
        RecurrenceFrequency.SEASONAL,
        RecurrenceFrequency.ANNUAL,
        RecurrenceFrequency.ONCE,
    ]

    with get_session() as session:
        query = (
            session.query(Task)
            .join(TaskSchedule, Task.id == TaskSchedule.task_id)
            .filter(Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]))
        )
        if property:
            prop = session.query(Property).filter(
                (Property.slug == property) | (Property.name.ilike(f"%{property}%"))
            ).first()
            if not prop:
                format_error(f"Property '{property}' not found")
                raise typer.Exit(1)
            query = query.filter(Task.property_id == prop.id)
        if assignee:
            staff = session.query(Staff).filter(
                (Staff.slug == assignee) | (Staff.name.ilike(f"%{assignee}%"))
            ).first()
            if not staff:
                format_error(f"Staff '{assignee}' not found")
                raise typer.Exit(1)
            query = query.filter(Task.assignee_id == staff.id)
        if frequency:
            query = query.filter(TaskSchedule.frequency == frequency)

        tasks = query.all()
        if not tasks:
            console.print("[dim]No recurring tasks found.[/dim]")
            return

        # Group by frequency
        from collections import defaultdict
        grouped: dict = defaultdict(list)
        for t in tasks:
            grouped[t.schedule.frequency].append(t)

        total = 0
        for freq in FREQUENCY_ORDER:
            freq_tasks = grouped.get(freq)
            if not freq_tasks:
                continue

            table = Table(title=f"{freq.value.upper()} ({len(freq_tasks)})", show_lines=False)
            table.add_column("Task", style="bold")
            table.add_column("Property")
            table.add_column("Assignee")
            table.add_column("Due", style="dim")

            for t in sorted(freq_tasks, key=lambda x: (x.property.name, x.title)):
                assignee_name = t.assignee.name if t.assignee else "[dim]—[/dim]"
                due = t.due_date.isoformat() if t.due_date else "—"
                table.add_row(t.title, t.property.name, assignee_name, due)

            console.print(table)
            console.print()
            total += len(freq_tasks)

        console.print(f"[dim]Total: {total} recurring task(s)[/dim]")


@app.command("delete")
def delete_task(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a task."""
    with get_session() as session:
        try:
            task = task_svc.get_task(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete task '{task.title}'?", abort=True)
        name = task_svc.delete_task(session, id_or_slug)
        format_success(f"Task '{name}' deleted")
