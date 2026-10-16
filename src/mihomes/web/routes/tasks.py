"""Task routes."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.task import RecurrenceFrequency, TaskPriority, TaskStatus
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.services import task as task_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()

#: Frequency view sections, most frequent first; one-time last. Keys are `RecurrenceFrequency`
#: values, so a new frequency added to the enum shows up as an unknown key and is grouped under
#: One-time by `group_by_frequency` rather than silently dropped.
FREQUENCY_ORDER = [
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("biweekly", "Every 2 weeks"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("seasonal", "Seasonal"),
    ("annual", "Annual"),
    ("once", "One-time"),
]


def group_by_frequency(tasks) -> dict[str, list]:
    """Tasks keyed by how often they repeat. No schedule, `once`, or an unlisted frequency → `once`."""
    groups: dict[str, list] = {key: [] for key, _ in FREQUENCY_ORDER}
    for task in tasks:
        freq = task.schedule.frequency.value if task.schedule else "once"
        groups.get(freq, groups["once"]).append(task)
    return groups


def _ctx(
    db: Session,
    property_id=None,
    status=None,
    overdue=False,
    due_week=False,
    assignee_id=None,
    sort: str | None = None,
    recurrence: str | None = None,
    form_error: str | None = None,
) -> dict:
    if overdue:
        tasks = task_svc.get_overdue_tasks(db)
    elif due_week:
        tasks = task_svc.get_upcoming_tasks(db, days=7)
    else:
        tasks = task_svc.list_tasks(
            db,
            property_id_or_slug=str(property_id) if property_id else None,
            status=TaskStatus(status) if status else None,
            assignee_id_or_slug=str(assignee_id) if assignee_id else None,
        )

    # Recurrence filter
    if recurrence == "recurring":
        tasks = [t for t in tasks if t.schedule and t.schedule.frequency.value != "once"]
    elif recurrence == "once":
        tasks = [t for t in tasks if not t.schedule or t.schedule.frequency.value == "once"]

    # Sort order
    if sort == "oldest":
        tasks = sorted(tasks, key=lambda t: t.created_at or t.id)
    else:
        tasks = sorted(tasks, key=lambda t: t.created_at or t.id, reverse=True)

    all_overdue = task_svc.get_overdue_tasks(db)
    overdue_ids = {t.id for t in all_overdue}

    # Kanban columns — exclude cancelled from board
    board_tasks = [t for t in tasks if t.status != TaskStatus.CANCELLED]
    columns = {
        "pending":     [t for t in board_tasks if t.status == TaskStatus.PENDING],
        "in_progress": [t for t in board_tasks if t.status == TaskStatus.IN_PROGRESS],
        "completed":   [t for t in board_tasks if t.status == TaskStatus.COMPLETED],
    }

    # Priority groups for grouped view
    priority_groups = {
        "urgent": [t for t in tasks if t.priority == TaskPriority.URGENT],
        "high":   [t for t in tasks if t.priority == TaskPriority.HIGH],
        "medium": [t for t in tasks if t.priority == TaskPriority.MEDIUM],
        "low":    [t for t in tasks if t.priority == TaskPriority.LOW],
    }

    frequency_groups = group_by_frequency(tasks)

    return {
        "page": "tasks",
        "tasks": tasks,
        "columns": columns,
        "priority_groups": priority_groups,
        "frequency_groups": frequency_groups,
        "frequency_meta": FREQUENCY_ORDER,
        # Form options in the same order as the Frequency view, one-time first for the default.
        "frequency_options": [FREQUENCY_ORDER[-1]] + FREQUENCY_ORDER[:-1],
        "form_error": form_error,
        "properties": prop_svc.list_properties(db),
        "staff": staff_svc.list_staff(db, category="Staff"),
        "overdue_ids": overdue_ids,
        "priorities": [p.value for p in TaskPriority],
        "statuses": [s.value for s in TaskStatus],
        "notes_map": {t.id: note_svc.list_notes(db, f"task:{t.id}") for t in tasks},
        "filter_property": property_id,
        "filter_status": status,
        "filter_overdue": overdue,
        "filter_due_week": due_week,
        "filter_assignee": assignee_id,
        "filter_sort": sort or "newest",
        "filter_recurrence": recurrence or "",
    }


# Row 5 (`task.manage`) is `SCOPED` for staff. The index has no single target property, so it is
# a COLLECTION route: Step 7 constrains the query by `scoped_property_ids()` rather than refusing
# the page (N5). Every other route here operates on one task and is therefore an ITEM route,
# whose target property Step 7 resolves from the task itself — the slug names the task, not the
# property, so the resolution is a lookup rather than a path parameter.
@router.get("/")
@declares("task.manage", Access.COLLECTION)
def list_tasks(
    request: Request,
    property_id: str | None = None,
    status: str | None = None,
    overdue: bool = False,
    due_week: bool = False,
    assignee_id: str | None = None,
    sort: str | None = None,
    recurrence: str | None = None,
    db: Session = Depends(get_db),
):
    # Blank and whitespace-only both mean "no filter". The `int()` these used to be
    # wrapped in is gone (ids are UUIDs now), but the empty-value guard has to stay —
    # an empty string reaching a UUID column is an error, not a no-op.
    pid = (property_id or "").strip() or None
    aid = (assignee_id or "").strip() or None
    return templates.TemplateResponse(
        request, "tasks.html",
        _ctx(db, pid, status or None, overdue, due_week, aid, sort, recurrence),
    )


@router.post("/", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def create_task(
    request: Request,
    title: str = Form(...),
    property_id: str = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(None),
    recurrence: str = Form("once"),
    season_spec: str = Form(""),
    db: Session = Depends(get_db),
):
    due = date.fromisoformat(due_date) if due_date else None
    try:
        task_svc.create_task(
            db,
            title=title,
            property_id_or_slug=str(property_id),
            priority=TaskPriority(priority),
            due_date=due,
            recurrence=_frequency(recurrence),
            season_spec=season_spec.strip() or None,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "tasks.html", _ctx(db, form_error=str(exc)), status_code=400
        )
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


def _frequency(value: str | None) -> RecurrenceFrequency:
    """A form's frequency value, or ONCE for blank/unknown — never a 500 on a bad option."""
    try:
        return RecurrenceFrequency((value or "once").strip().lower())
    except ValueError:
        return RecurrenceFrequency.ONCE


@router.post("/{slug}/complete", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def complete_task(request: Request, slug: str, db: Session = Depends(get_db)):
    """Done. A repeating task resets in place to its next date; a one-time task is completed."""
    try:
        task_svc.complete_task(db, slug)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "tasks.html", _ctx(db, form_error=str(exc)), status_code=400
        )
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/{slug}/archive", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def archive_task(request: Request, slug: str, db: Session = Depends(get_db)):
    """Clear one completed task off the list (kept in history, not deleted)."""
    try:
        task_svc.archive_task(db, slug)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "tasks.html", _ctx(db, form_error=str(exc)), status_code=400
        )
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/clear-completed", response_class=HTMLResponse)
@declares("task.manage", Access.COLLECTION)
def clear_completed(request: Request, db: Session = Depends(get_db)):
    """Clear every completed task off the list. COLLECTION: staff clear only tasks in their scope,
    because the query layer already narrows `Task` to their properties."""
    task_svc.clear_completed(db)
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/{slug}/edit", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def edit_task(
    request: Request,
    slug: str,
    title: str = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(""),
    description: str = Form(""),
    status: str = Form(""),
    recurrence: str | None = Form(None),
    season_spec: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs = dict(
        title=title,
        priority=TaskPriority(priority),
        due_date=date.fromisoformat(due_date) if due_date else None,
        description=description or None,
    )
    # Choosing "Completed" here must do exactly what the Done button does — reset a repeating
    # task in place, complete a one-time one. Writing the status directly skipped all of that, so
    # it is held back and applied last, after any frequency change below takes effect.
    complete_now = status == TaskStatus.COMPLETED.value
    if status and not complete_now:
        kwargs["status"] = TaskStatus(status)
    try:
        task = task_svc.update_task(db, slug, **kwargs)
        # Only when the form sends it — a caller posting to /edit without a frequency field must
        # not wipe the schedule (the slug may change with the title, so use the task's own).
        if recurrence is not None:
            task_svc.set_task_frequency(
                db, task.slug, _frequency(recurrence), season_spec.strip() or None
            )
        if complete_now and task.status != TaskStatus.COMPLETED:
            task_svc.complete_task(db, task.slug)
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request, "tasks.html", _ctx(db, form_error=str(exc)), status_code=400
        )
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def delete_task(request: Request, slug: str, db: Session = Depends(get_db)):
    task_svc.delete_task(db, slug)
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


# Notes on a task are governed by the task's own row, not by a separate action: `note` is
# account-level in §4.1's classification, but a note *attached to a task* inherits that task's
# property scope. Declaring `task.manage` keeps the two from diverging — a staff member who may
# work the task may annotate it, and one who may not, cannot.
@router.post("/{slug}/notes", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    task = task_svc.get_task(db, slug)
    note_svc.add_note(db, f"task:{task.id}", content)
    notes = note_svc.list_notes(db, f"task:{task.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/tasks/{slug}/notes",
        "delete_url_prefix": f"/tasks/{slug}/notes",
    })


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def delete_note(request: Request, slug: str, note_id: UUID, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    task = task_svc.get_task(db, slug)
    notes = note_svc.list_notes(db, f"task:{task.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/tasks/{slug}/notes",
        "delete_url_prefix": f"/tasks/{slug}/notes",
    })
