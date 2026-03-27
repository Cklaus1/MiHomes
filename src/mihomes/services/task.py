"""Task service — CRUD with recurrence support."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.models.task import (
    RecurrenceFrequency,
    Task,
    TaskPriority,
    TaskSchedule,
    TaskStatus,
)
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.recurrence import calculate_next_due
from mihomes.services.slug import (
    ensure_unique_slug,
    generate_slug,
    resolve_identifier,
)


def create_task(
    session: Session,
    title: str,
    property_id_or_slug: str,
    *,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    assignee_id_or_slug: str | None = None,
    due_date: date | None = None,
    recurrence: RecurrenceFrequency = RecurrenceFrequency.ONCE,
    season_spec: str | None = None,
    slug: str | None = None,
) -> Task:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    assignee_id = None
    if assignee_id_or_slug:
        assignee = resolve_identifier(session, Staff, assignee_id_or_slug)
        assignee_id = assignee.id

    slug = ensure_unique_slug(session, Task, slug or generate_slug(title))

    task = Task(
        title=title,
        slug=slug,
        description=description,
        property_id=prop.id,
        assignee_id=assignee_id,
        priority=priority,
        due_date=due_date,
    )
    session.add(task)
    session.flush()

    # Create schedule for recurring tasks
    if recurrence != RecurrenceFrequency.ONCE:
        next_due = None
        if due_date:
            next_due = calculate_next_due(
                recurrence.value, due_date,
                climate_zone=prop.climate_zone,
                season_spec=season_spec,
            )
        schedule = TaskSchedule(
            task_id=task.id,
            frequency=recurrence,
            season_spec=season_spec,
            next_due=next_due,
        )
        session.add(schedule)
        session.flush()

    record_change(session, "task", task.id, "create", snapshot_instance(task))
    return task


def list_tasks(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    assignee_id_or_slug: str | None = None,
    overdue: bool = False,
) -> list[Task]:
    query = session.query(Task)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Task.property_id == prop.id)
    if status:
        query = query.filter(Task.status == status)
    if priority:
        query = query.filter(Task.priority == priority)
    if assignee_id_or_slug:
        assignee = resolve_identifier(session, Staff, assignee_id_or_slug)
        query = query.filter(Task.assignee_id == assignee.id)
    if overdue:
        query = query.filter(
            Task.due_date < date.today(),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
    return query.order_by(Task.due_date.asc().nullslast(), Task.priority).all()


def get_task(session: Session, id_or_slug: str) -> Task:
    return resolve_identifier(session, Task, id_or_slug)


def update_task(session: Session, id_or_slug: str, **kwargs) -> Task:
    task = resolve_identifier(session, Task, id_or_slug)
    old_snap = snapshot_instance(task)
    if "title" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Task, generate_slug(kwargs["title"]), exclude_id=task.id)
    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)
    session.flush()
    new_snap = snapshot_instance(task)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "task", task.id, "update", changes)
    return task


def complete_task(session: Session, id_or_slug: str, notes: str | None = None) -> Task:
    """Complete a task and create the next occurrence if recurring."""
    task = resolve_identifier(session, Task, id_or_slug)
    old_snap = snapshot_instance(task)

    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(timezone.utc)
    task.completion_notes = notes
    session.flush()

    new_snap = snapshot_instance(task)
    changes = diff_instance(old_snap, new_snap)
    record_change(session, "task", task.id, "update", changes)

    # Advance recurring task
    if task.schedule and task.schedule.frequency != RecurrenceFrequency.ONCE:
        _advance_recurring_task(session, task)

    return task


def _advance_recurring_task(session: Session, completed_task: Task) -> Task | None:
    """Create the next occurrence of a recurring task."""
    schedule = completed_task.schedule
    current_due = completed_task.due_date or date.today()

    prop = completed_task.property
    next_due = calculate_next_due(
        schedule.frequency.value,
        current_due,
        climate_zone=prop.climate_zone,
        season_spec=schedule.season_spec,
    )

    if next_due is None:
        return None

    # Create new task for next occurrence
    new_slug = ensure_unique_slug(session, Task, generate_slug(completed_task.title))
    new_task = Task(
        title=completed_task.title,
        slug=new_slug,
        description=completed_task.description,
        property_id=completed_task.property_id,
        assignee_id=completed_task.assignee_id,
        priority=completed_task.priority,
        due_date=next_due,
    )
    session.add(new_task)
    session.flush()

    # Create new schedule
    new_schedule = TaskSchedule(
        task_id=new_task.id,
        frequency=schedule.frequency,
        season_spec=schedule.season_spec,
        next_due=calculate_next_due(
            schedule.frequency.value, next_due,
            climate_zone=prop.climate_zone,
            season_spec=schedule.season_spec,
        ),
        last_generated=date.today(),
    )
    session.add(new_schedule)
    session.flush()

    record_change(session, "task", new_task.id, "create", snapshot_instance(new_task))
    return new_task


def get_upcoming_tasks(
    session: Session,
    days: int = 14,
    property_id_or_slug: str | None = None,
) -> list[Task]:
    today = date.today()
    end = today + timedelta(days=days)
    query = session.query(Task).filter(
        Task.due_date >= today,
        Task.due_date <= end,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Task.property_id == prop.id)
    return query.order_by(Task.due_date).all()


def get_overdue_tasks(
    session: Session,
    property_id_or_slug: str | None = None,
) -> list[Task]:
    query = session.query(Task).filter(
        Task.due_date < date.today(),
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Task.property_id == prop.id)
    return query.order_by(Task.due_date).all()


def delete_task(session: Session, id_or_slug: str) -> str:
    task = resolve_identifier(session, Task, id_or_slug)
    name = task.title
    record_change(session, "task", task.id, "delete", snapshot_instance(task))
    session.delete(task)
    session.flush()
    return name
