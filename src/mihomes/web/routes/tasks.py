"""Task routes."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.task import TaskPriority, TaskStatus
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.services import task as task_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(
    db: Session,
    property_id=None,
    status=None,
    overdue=False,
    due_week=False,
    assignee_id=None,
    sort: str | None = None,
    recurrence: str | None = None,
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

    return {
        "page": "tasks",
        "tasks": tasks,
        "columns": columns,
        "priority_groups": priority_groups,
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


@router.get("/")
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
    pid = property_id.strip() or None if property_id else None
    aid = assignee_id.strip() or None if assignee_id else None
    return templates.TemplateResponse(
        request, "tasks.html",
        _ctx(db, pid, status or None, overdue, due_week, aid, sort, recurrence),
    )


@router.post("/", response_class=HTMLResponse)
def create_task(
    request: Request,
    title: str = Form(...),
    property_id: str = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(None),
    db: Session = Depends(get_db),
):
    due = date.fromisoformat(due_date) if due_date else None
    task_svc.create_task(
        db,
        title=title,
        property_id_or_slug=str(property_id),
        priority=TaskPriority(priority),
        due_date=due,
    )
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/{slug}/complete", response_class=HTMLResponse)
def complete_task(request: Request, slug: str, db: Session = Depends(get_db)):
    task_svc.complete_task(db, slug)
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_task(
    request: Request,
    slug: str,
    title: str = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(""),
    description: str = Form(""),
    status: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs = dict(
        title=title,
        priority=TaskPriority(priority),
        due_date=date.fromisoformat(due_date) if due_date else None,
        description=description or None,
    )
    if status:
        kwargs["status"] = TaskStatus(status)
    task_svc.update_task(db, slug, **kwargs)
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_task(request: Request, slug: str, db: Session = Depends(get_db)):
    task_svc.delete_task(db, slug)
    return templates.TemplateResponse(request, "tasks.html", _ctx(db))


@router.post("/{slug}/notes", response_class=HTMLResponse)
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
def delete_note(request: Request, slug: str, note_id: UUID, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    task = task_svc.get_task(db, slug)
    notes = note_svc.list_notes(db, f"task:{task.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/tasks/{slug}/notes",
        "delete_url_prefix": f"/tasks/{slug}/notes",
    })
