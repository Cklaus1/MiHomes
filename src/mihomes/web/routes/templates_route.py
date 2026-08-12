"""Templates route."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.task import TaskPriority
from mihomes.services import property as prop_svc
from mihomes.services import template as tmpl_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session, flash: str | None = None) -> dict:
    return {
        "page": "templates",
        "templates": tmpl_svc.list_templates(db),
        "properties": prop_svc.list_properties(db),
        "priorities": [p.value for p in TaskPriority],
        "flash": flash,
    }


@router.get("/")
def list_templates(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "templates.html", _ctx(db))


@router.post("/", response_class=HTMLResponse)
def create_template(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    steps: str = Form(""),
    db: Session = Depends(get_db),
):
    step_list = [s.strip() for s in steps.splitlines() if s.strip()]
    tmpl_svc.create_template(
        db, name,
        description=description or None,
        steps=step_list or None,
    )
    return templates.TemplateResponse(request, "templates.html", _ctx(db))


@router.post("/{slug}/run", response_class=HTMLResponse)
def run_template(
    request: Request,
    slug: str,
    property_id: str = Form(...),
    due_date: str = Form(""),
    priority: str = Form("medium"),
    db: Session = Depends(get_db),
):
    due = date.fromisoformat(due_date) if due_date else None
    tasks = tmpl_svc.run_template(
        db, slug, str(property_id),
        due_date=due,
        priority=TaskPriority(priority),
    )
    flash = f"Created {len(tasks)} task{'s' if len(tasks) != 1 else ''} from template."
    return templates.TemplateResponse(request, "templates.html", _ctx(db, flash=flash))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_template(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
):
    tmpl_svc.delete_template(db, slug)
    return templates.TemplateResponse(request, "templates.html", _ctx(db))
