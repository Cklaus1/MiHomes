"""Task routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.task import TaskPriority, TaskStatus
from mihomes.services import property as prop_svc
from mihomes.services import task as task_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def list_tasks(
    request: Request,
    property_id: int | None = None,
    status: str | None = None,
    priority: str | None = None,
    db: Session = Depends(get_db),
):
    tasks = task_svc.list_tasks(db, property_id=property_id, status=status)
    properties = prop_svc.list_properties(db)
    overdue = task_svc.get_overdue_tasks(db)
    overdue_ids = {t.id for t in overdue}
    return templates.TemplateResponse(
        "tasks.html",
        {
            "request": request,
            "page": "tasks",
            "tasks": tasks,
            "properties": properties,
            "overdue_ids": overdue_ids,
            "filter_property": property_id,
            "filter_status": status,
            "priorities": [p.value for p in TaskPriority],
            "statuses": [s.value for s in TaskStatus],
        },
    )


@router.post("/{slug}/complete", response_class=HTMLResponse)
def complete_task(request: Request, slug: str, db: Session = Depends(get_db)):
    task, next_task = task_svc.complete_task(db, slug)
    return templates.TemplateResponse(
        "partials/task_row.html",
        {"request": request, "task": task, "next_task": next_task, "overdue_ids": set()},
    )


@router.post("/", response_class=HTMLResponse)
def create_task(
    request: Request,
    title: str = Form(...),
    property_id: int = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(None),
    db: Session = Depends(get_db),
):
    from datetime import date
    due = date.fromisoformat(due_date) if due_date else None
    task_svc.create_task(
        db,
        title=title,
        property_id=property_id,
        priority=TaskPriority(priority),
        due_date=due,
    )
    tasks = task_svc.list_tasks(db)
    overdue = task_svc.get_overdue_tasks(db)
    overdue_ids = {t.id for t in overdue}
    return templates.TemplateResponse(
        "partials/task_list.html",
        {"request": request, "tasks": tasks, "overdue_ids": overdue_ids},
    )
