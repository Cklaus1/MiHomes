"""Staff routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.staff import CATEGORY_ORDER, StaffRole, category_for_role, is_staff_role
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session) -> dict:
    staff = staff_svc.list_staff(db)
    # Group people by their derived directory category (Staff / Resident / ...).
    grouped: dict[str, list] = {cat: [] for cat in CATEGORY_ORDER}
    for m in staff:
        grouped[category_for_role(m.role)].append(m)
    # Role options split into Staff roles vs. other people-types for the dropdown.
    role_groups = [
        ("Staff", [r.value for r in StaffRole if is_staff_role(r)]),
        ("Other", [r.value for r in StaffRole if not is_staff_role(r)]),
    ]
    return {
        "page": "staff",
        "staff": staff,
        "grouped": grouped,
        "category_order": CATEGORY_ORDER,
        "category_for_role": category_for_role,
        "properties": prop_svc.list_properties(db),
        "roles": [r.value for r in StaffRole],
        "role_groups": role_groups,
        "notes_map": {m.id: note_svc.list_notes(db, f"staff:{m.id}") for m in staff},
    }


@router.get("/")
@declares("member.manage", Access.ACCOUNT)
def list_staff(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def create_staff(
    request: Request,
    name: str = Form(...),
    role: str = Form("other"),
    phone: str = Form(""),
    email: str = Form(""),
    property_ids: list[str] = Form(default=[]),
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
@declares("member.manage", Access.ACCOUNT)
def assign_property(
    request: Request,
    slug: str,
    property_id: str = Form(...),
    db: Session = Depends(get_db),
):
    staff_svc.assign_to_property(db, slug, str(property_id))
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/{slug}/edit", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def edit_staff(
    request: Request,
    slug: str,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    role: str = Form(""),
    active: list[str] = Form(default=[]),
    property_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    kwargs = {"name": name, "phone": phone or None, "email": email or None}
    if role:
        kwargs["role"] = StaffRole(role)
    # M17: hidden active=0 + checkbox active=1 → active iff any "1" submitted.
    kwargs["active"] = "1" in active
    member = staff_svc.update_staff(db, slug, **kwargs)
    # Sync property assignments to the submitted set.
    #
    # Both sides must be strings. Form values arrive as strings while `p.id` is a
    # uuid.UUID after G6.1, so a mixed comparison makes every element look distinct:
    # `selected - current` would be all of `selected` and `current - selected` all of
    # `current`, re-assigning and re-removing everything on every edit.
    selected = set(property_ids)
    current = {str(p.id) for p in member.properties}
    for pid in selected - current:
        staff_svc.assign_to_property(db, member.slug, str(pid))
    for pid in current - selected:
        staff_svc.remove_from_property(db, member.slug, str(pid))
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def delete_staff(request: Request, slug: str, db: Session = Depends(get_db)):
    staff_svc.delete_staff(db, slug)
    return templates.TemplateResponse(request, "staff.html", _ctx(db))


@router.post("/{slug}/notes", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
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
@declares("member.manage", Access.ACCOUNT)
def delete_note(request: Request, slug: str, note_id: UUID, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    member = staff_svc.get_staff(db, slug)
    notes = note_svc.list_notes(db, f"staff:{member.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/staff/{slug}/notes",
        "delete_url_prefix": f"/staff/{slug}/notes",
    })
