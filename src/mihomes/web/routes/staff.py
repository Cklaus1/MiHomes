"""Staff routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.staff import StaffRole
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def list_staff(request: Request, db: Session = Depends(get_db)):
    staff = staff_svc.list_staff(db)
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        "staff.html",
        {
            "request": request,
            "page": "staff",
            "staff": staff,
            "properties": properties,
            "roles": [r.value for r in StaffRole],
        },
    )


@router.post("/", response_class=HTMLResponse)
def create_staff(
    request: Request,
    name: str = Form(...),
    role: str = Form("other"),
    phone: str = Form(""),
    email: str = Form(""),
    property_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    member = staff_svc.create_staff(
        db,
        name=name,
        role=StaffRole(role),
        phone=phone or None,
        email=email or None,
    )
    for pid in property_ids:
        staff_svc.assign_to_property(db, member.slug, pid)
    staff = staff_svc.list_staff(db)
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        "partials/staff_list.html",
        {"request": request, "staff": staff, "properties": properties},
    )


@router.post("/{slug}/assign", response_class=HTMLResponse)
def assign_property(
    request: Request,
    slug: str,
    property_id: int = Form(...),
    db: Session = Depends(get_db),
):
    member = staff_svc.assign_to_property(db, slug, property_id)
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        "partials/staff_card.html",
        {"request": request, "member": member, "properties": properties},
    )
