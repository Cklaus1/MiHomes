"""Staff routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.staff import StaffRole
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session) -> dict:
    staff = staff_svc.list_staff(db)
    return {
        "page": "staff",
        "staff": staff,
        "properties": prop_svc.list_properties(db),
        "roles": [r.value for r in StaffRole],
        "notes_map": {m.id: note_svc.list_notes(db, f"staff:{m.id}") for m in staff},
    }


@router.get("/")
def list_staff(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


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
        staff_svc.assign_to_property(db, member.slug, str(pid))
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/{slug}/assign", response_class=HTMLResponse)
def assign_property(
    request: Request,
    slug: str,
    property_id: int = Form(...),
    db: Session = Depends(get_db),
):
    staff_svc.assign_to_property(db, slug, str(property_id))
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_staff(
    request: Request,
    slug: str,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    role: str = Form(""),
    active: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs = {"name": name, "phone": phone or None, "email": email or None}
    if role:
        kwargs["role"] = StaffRole(role)
    kwargs["active"] = active == "1"
    staff_svc.update_staff(db, slug, **kwargs)
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_staff(request: Request, slug: str, db: Session = Depends(get_db)):
    staff_svc.delete_staff(db, slug)
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/{slug}/notes", response_class=HTMLResponse)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    member = staff_svc.get_staff(db, slug)
    note_svc.add_note(db, f"staff:{member.id}", content)
    notes = note_svc.list_notes(db, f"staff:{member.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/staff/{slug}/notes",
        "delete_url_prefix": f"/staff/{slug}/notes",
    })


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, slug: str, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    member = staff_svc.get_staff(db, slug)
    notes = note_svc.list_notes(db, f"staff:{member.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/staff/{slug}/notes",
        "delete_url_prefix": f"/staff/{slug}/notes",
    })
