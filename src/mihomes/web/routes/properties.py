"""Property routes."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.property import PropertyStatus, PropertyType
from mihomes.models.task import TaskStatus
from mihomes.services import issue as issue_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import task as task_svc
from mihomes.services.health_score import compute_property_health
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def list_properties(request: Request, db: Session = Depends(get_db)):
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        request,
        "properties.html",
        {"page": "properties", "properties": properties},
    )


@router.get("/new")
def new_property_form(request: Request):
    return templates.TemplateResponse(
        request,
        "property_form.html",
        {
            "page": "properties",
            "property": None,
            "property_types": [t.value for t in PropertyType],
            "property_statuses": [s.value for s in PropertyStatus],
        },
    )


@router.post("/", response_class=HTMLResponse)
def create_property(
    request: Request,
    name: str = Form(...),
    address: str = Form(""),
    property_type: str = Form("other"),
    status: str = Form("open"),
    db: Session = Depends(get_db),
):
    prop_svc.create_property(
        db,
        name=name,
        address=address or None,
        property_type=PropertyType(property_type),
        status=PropertyStatus(status),
    )
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        request,
        "partials/property_list.html",
        {"properties": properties},
        headers={"HX-Push-Url": "/properties"},
    )


@router.get("/{slug}")
def property_detail(request: Request, slug: str, db: Session = Depends(get_db)):
    prop = prop_svc.get_property(db, slug)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    health = compute_property_health(db, prop.id)
    open_tasks = task_svc.list_tasks(db, property_id_or_slug=slug, status=TaskStatus.PENDING)
    open_issues = issue_svc.list_issues(db, property_id_or_slug=slug, open_only=True)
    assigned_staff = staff_svc.list_by_property(db, slug)
    spaces = space_svc.list_spaces(db, property_id_or_slug=slug)
    return templates.TemplateResponse(
        request,
        "property_detail.html",
        {
            "page": "properties",
            "prop": prop,
            "health": health,
            "open_tasks": open_tasks,
            "open_issues": open_issues,
            "assigned_staff": assigned_staff,
            "spaces": spaces,
            "space_types": ["bedroom", "bathroom", "kitchen", "living", "dining", "office", "entertainment", "storage", "garage", "outdoor", "other"],
            "property_types": [t.value for t in PropertyType],
            "property_statuses": [s.value for s in PropertyStatus],
        },
    )


@router.post("/{slug}/occupy", response_class=HTMLResponse)
def occupy(request: Request, slug: str, db: Session = Depends(get_db)):
    prop = prop_svc.occupy_property(db, slug)
    return templates.TemplateResponse(
        request,
        "partials/property_status_badge.html",
        {"prop": prop},
    )


@router.post("/{slug}/vacate", response_class=HTMLResponse)
def vacate(request: Request, slug: str, db: Session = Depends(get_db)):
    prop = prop_svc.vacate_property(db, slug)
    return templates.TemplateResponse(
        request,
        "partials/property_status_badge.html",
        {"prop": prop},
    )


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_property(
    request: Request,
    slug: str,
    name: str = Form(""),
    address: str = Form(""),
    property_type: str = Form(""),
    status: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs: dict = {}
    if name:
        kwargs["name"] = name
    if address is not None:
        kwargs["address"] = address or None
    if property_type:
        kwargs["property_type"] = PropertyType(property_type)
    if status:
        kwargs["status"] = PropertyStatus(status)
    prop_svc.update_property(db, slug, **kwargs)
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        request,
        "properties.html",
        {"page": "properties", "properties": properties},
    )


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_property(request: Request, slug: str, db: Session = Depends(get_db)):
    prop_svc.delete_property(db, slug)
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        request,
        "properties.html",
        {"page": "properties", "properties": properties},
    )


def _rooms_ctx(db, slug: str) -> dict:
    spaces = space_svc.list_spaces(db, property_id_or_slug=slug)
    return {
        "spaces": spaces,
        "prop_slug": slug,
        "space_types": ["bedroom", "bathroom", "kitchen", "living", "dining", "office", "entertainment", "storage", "garage", "outdoor", "other"],
    }


@router.post("/{slug}/spaces", response_class=HTMLResponse)
def create_space(
    request: Request,
    slug: str,
    name: str = Form(...),
    space_type: str = Form(""),
    db: Session = Depends(get_db),
):
    space_svc.create_space(db, name, slug, space_type=space_type or None)
    return templates.TemplateResponse(request, "partials/rooms_list.html", _rooms_ctx(db, slug))


@router.post("/{slug}/spaces/{space_slug}/delete", response_class=HTMLResponse)
def delete_space(request: Request, slug: str, space_slug: str, db: Session = Depends(get_db)):
    space_svc.delete_space(db, space_slug)
    return templates.TemplateResponse(request, "partials/rooms_list.html", _rooms_ctx(db, slug))
