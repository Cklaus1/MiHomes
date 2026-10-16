"""Task service — CRUD with recurrence support."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.models.task import (
    RecurrenceFrequency,
    Task,
    TaskCategory,
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
from mihomes.services.update_helpers import safe_update


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
    estimated_hours: float | None = None,
    zone_id: int | None = None,
    category: TaskCategory | None = None,
) -> Task:
    if len(title) > 300:
        raise ValueError(f"Task title too long (max 300 chars, got {len(title)})")
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
        estimated_hours=estimated_hours,
        zone_id=zone_id,
        category=category,
    )
    session.add(task)
    session.flush()

    if recurrence == RecurrenceFrequency.SEASONAL:
        _validate_season_spec(prop, season_spec)

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
    category: TaskCategory | None = None,
    include_archived: bool = False,
) -> list[Task]:
    query = session.query(Task)
    if not include_archived:
        # Completed one-time tasks the user cleared off the list (archive_task) stay in history.
        query = query.filter(Task.archived_at.is_(None))
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
    if category:
        query = query.filter(Task.category == category)
    if overdue:
        query = query.filter(
            Task.due_date < date.today(),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
    return query.order_by(Task.due_date.asc().nullslast(), Task.priority).all()


def get_task(session: Session, id_or_slug: str) -> Task:
    return resolve_identifier(session, Task, id_or_slug)


def _validate_season_spec(prop: Property, season_spec: str | None) -> None:
    """Seasonal recurrence needs seasons valid for the property's climate zone."""
    if not season_spec:
        raise ValueError("Season spec required for seasonal recurrence (e.g., 'spring,fall')")
    from mihomes.services.recurrence import SEASON_CALENDAR
    zone = (prop.climate_zone or "default").lower()
    calendar = SEASON_CALENDAR.get(zone, SEASON_CALENDAR["default"])
    valid_seasons = set(calendar.keys())
    requested = {s.strip().lower() for s in season_spec.split(",")}
    invalid = requested - valid_seasons
    if invalid:
        raise ValueError(f"Invalid season(s) {invalid} for climate zone '{zone}'. Valid: {valid_seasons}")


def set_task_frequency(
    session: Session,
    id_or_slug: str,
    frequency: RecurrenceFrequency,
    season_spec: str | None = None,
) -> Task:
    """Change how often a task repeats — the edit-side counterpart of `create_task(recurrence=…)`.

    ONCE removes the schedule; any other frequency creates it or updates it in place. `next_due`
    is recomputed from the task's due date the same way creation computes it, so changing the
    frequency never leaves a stale next date behind. Audited as a `frequency` change on the task.
    """
    task = resolve_identifier(session, Task, id_or_slug)
    schedule = task.schedule
    old = schedule.frequency.value if schedule else RecurrenceFrequency.ONCE.value
    old_season = schedule.season_spec if schedule else None

    if frequency == RecurrenceFrequency.SEASONAL:
        _validate_season_spec(task.property, season_spec)
    else:
        season_spec = None

    if old == frequency.value and old_season == season_spec:
        return task

    if frequency == RecurrenceFrequency.ONCE:
        if schedule is not None:
            session.delete(schedule)
    else:
        next_due = None
        if task.due_date:
            next_due = calculate_next_due(
                frequency.value, task.due_date,
                climate_zone=task.property.climate_zone,
                season_spec=season_spec,
            )
        if schedule is None:
            session.add(TaskSchedule(
                task_id=task.id, frequency=frequency,
                season_spec=season_spec, next_due=next_due,
            ))
        else:
            schedule.frequency = frequency
            schedule.season_spec = season_spec
            schedule.next_due = next_due
    session.flush()
    session.expire(task, ["schedule"])
    record_change(session, "task", task.id, "update",
                  {"frequency": {"old": old, "new": frequency.value}})
    return task


def update_task(session: Session, id_or_slug: str, **kwargs) -> Task:
    task = resolve_identifier(session, Task, id_or_slug)
    old_snap = snapshot_instance(task)
    if "title" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Task, generate_slug(kwargs["title"]), exclude_id=task.id)
    safe_update(task, kwargs)
    session.flush()
    new_snap = snapshot_instance(task)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "task", task.id, "update", changes)
    return task


#: Safety bound on rolling a due date forward. A daily task overdue by years needs ~1,000 steps;
#: this only stops a pathological schedule (one that never advances) from looping.
_MAX_ROLL_STEPS = 5000


def roll_forward(
    frequency: str,
    due: date | None,
    today: date,
    *,
    climate_zone: str | None = None,
    season_spec: str | None = None,
) -> date | None:
    """The next due date after `today`, keeping the task's rhythm (user decision, 2026-10-16).

    Steps `calculate_next_due` from the current due date until it passes today, so a late task
    skips the dates it missed instead of coming back already overdue: a weekly Monday task done
    on Wednesday returns next Monday. A task with no due date starts from today. Returns None for
    a frequency that yields no next date.
    """
    current = due or today
    for _ in range(_MAX_ROLL_STEPS):
        nxt = calculate_next_due(frequency, current, climate_zone=climate_zone,
                                 season_spec=season_spec)
        if nxt is None or nxt <= current:
            return None
        if nxt > today:
            return nxt
        current = nxt
    return None


#: How recent a completion must be for a repeat "done" to count as the same click. Long enough to
#: absorb a redelivered gateway message; short enough that finishing the next occurrence early,
#: on a later visit, is never mistaken for a duplicate.
_DUPLICATE_WINDOW = timedelta(minutes=10)


def _is_duplicate_done(session: Session, task: Task, now: datetime) -> bool:
    """True when a completion moments ago already rolled the task to its current due date.

    Each completion stores the due date it rolled the task TO (`rolled_to`). A second "done" for
    that same occurrence — a double-click, or a redelivered Telegram message — finds the task's
    due date still equal to `rolled_to` and the completion only seconds old. Finishing the next
    occurrence early on a later visit falls outside the window and is allowed.
    """
    from mihomes.models.task import TaskCompletion

    last = (
        session.query(TaskCompletion)
        .filter(TaskCompletion.task_id == task.id)
        .order_by(TaskCompletion.completed_at.desc())
        .first()
    )
    return (
        last is not None
        and last.rolled_to is not None
        and last.rolled_to == task.due_date
        and now - last.completed_at < _DUPLICATE_WINDOW
    )


def complete_task(
    session: Session,
    id_or_slug: str,
    notes: str | None = None,
    completed_by_staff_id=None,
) -> tuple[Task, Task | None]:
    """Mark a task done.

    - **One-time** task: status COMPLETED, as before. It stays on the list until archived.
    - **Repeating** task: the SAME row resets in place — due date rolls forward on its rhythm to
      the next date after the estate's today, status back to pending. No new row, so the list
      never grows. Each completion is recorded in `task_completions` (reports, staff stats).

    Returns `(task, next_task)`; for a repeating task both are the same, reset row — callers that
    read `next_task.due_date` keep working. **Idempotent for repeating tasks**: a task already
    rolled past today (e.g. a redelivered Telegram "done") is returned unchanged, not advanced a
    second time.
    """
    from mihomes.models.task import TaskCompletion
    from mihomes.services.config_service import estate_today

    task = resolve_identifier(session, Task, id_or_slug)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise ValueError(f"Task is already {task.status.value}. Cannot complete again.")

    recurring = bool(task.schedule) and task.schedule.frequency != RecurrenceFrequency.ONCE
    today = estate_today(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if recurring and _is_duplicate_done(session, task, now):
        # A completion moments ago already advanced this occurrence (double-click, or a
        # redelivered Telegram "done"). Advancing again would skip one ahead.
        return task, task

    old_snap = snapshot_instance(task)
    completed_due = task.due_date

    completion = TaskCompletion(
        task_id=task.id,
        property_id=task.property_id,
        completed_by_staff_id=completed_by_staff_id or task.assignee_id,
        completed_at=now,
        due_date=completed_due,
        notes=notes,
    )
    session.add(completion)

    next_task = None
    if recurring:
        schedule = task.schedule
        next_due = roll_forward(
            schedule.frequency.value, task.due_date, today,
            climate_zone=task.property.climate_zone, season_spec=schedule.season_spec,
        )
        if next_due is None:
            # A schedule that yields no next date (e.g. seasonal with an unknown season) ends
            # like a one-time task rather than silently staying open.
            task.status = TaskStatus.COMPLETED
            task.completed_at = now
            task.completion_notes = notes
        else:
            task.due_date = next_due
            completion.rolled_to = next_due
            # The calendar event (if any) was for the occurrence just done. Each occurrence used
            # to be a new row with no event id, so calendar sync pushed every one; clearing it
            # keeps that, now that the row is reused.
            task.gcal_event_id = None
            task.status = TaskStatus.PENDING
            task.completed_at = None
            task.completion_notes = notes
            schedule.next_due = roll_forward(
                schedule.frequency.value, next_due, next_due,
                climate_zone=task.property.climate_zone, season_spec=schedule.season_spec,
            )
            schedule.last_generated = today
            next_task = task
    else:
        task.status = TaskStatus.COMPLETED
        task.completed_at = now
        task.completion_notes = notes
    session.flush()

    changes = diff_instance(old_snap, snapshot_instance(task))
    changes["completed"] = {"old": None, "new": str(completed_due) if completed_due else "done"}
    record_change(session, "task", task.id, "update", changes)

    # Auto-resolve any alerts for this task — the occurrence they were about is done.
    from mihomes.models.alert import Alert, AlertStatus
    stale_alerts = session.query(Alert).filter(
        Alert.source_entity_type == "task",
        Alert.source_entity_id == task.id,
        Alert.status != AlertStatus.RESOLVED,
    ).all()
    for alert in stale_alerts:
        alert.status = AlertStatus.RESOLVED
    if stale_alerts:
        session.flush()

    return task, next_task


def is_done_for_now(task: Task, today: date) -> bool:
    """A repeating task whose current occurrence is finished and isn't due again yet.

    Completing a repeating task rolls it to its next date and stores it as pending (so scheduling,
    overdue checks and alerts all keep working), but the board shows it under **Done** until that
    next date arrives — then it moves back to To Do on its own, with no background job (user,
    2026-10-16). "Finished" means the latest completion is the one that rolled the task to its
    current due date: if the date is later moved by hand, the task is due again, not done.
    """
    if task.status != TaskStatus.PENDING or not task.due_date or task.due_date <= today:
        return False
    if not task.schedule or task.schedule.frequency == RecurrenceFrequency.ONCE:
        return False
    latest = task.completions[0] if task.completions else None
    return latest is not None and latest.rolled_to == task.due_date


def archive_task(session: Session, id_or_slug: str) -> Task:
    """Clear a completed one-time task off the list. Kept, not deleted — history is unaffected."""
    task = resolve_identifier(session, Task, id_or_slug)
    if task.status != TaskStatus.COMPLETED:
        raise ValueError("Only completed tasks can be cleared from the list.")
    if task.archived_at is None:
        task.archived_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.flush()
        record_change(session, "task", task.id, "update", {"archived": {"old": False, "new": True}})
    return task


def clear_completed(session: Session, property_id_or_slug: str | None = None) -> int:
    """Archive every completed, not-yet-archived task. Returns how many were cleared."""
    q = session.query(Task).filter(
        Task.status == TaskStatus.COMPLETED, Task.archived_at.is_(None)
    )
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        q = q.filter(Task.property_id == prop.id)
    tasks = q.all()
    for task in tasks:
        archive_task(session, task.slug)
    return len(tasks)


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
