"""Staff routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.document import DocumentType
from mihomes.models.staff import CATEGORY_ORDER, StaffRole, category_for_role, is_staff_role
from mihomes.services import document as doc_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.web.deps import get_db, templates, with_toast
from mihomes.web.forms import read_document_upload

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
        "docs_map": {m.id: doc_svc.list_documents(db, entity_type="staff", entity_id=m.id) for m in staff},
    }


@router.get("/")
@declares("staff.view_own", Access.ACCOUNT)
def list_staff(request: Request, db: Session = Depends(get_db)):
    """The directory. **`staff.view_own`, not `member.manage` — U6b.**

    §4.1 classifies `staff` as `PERSONNEL`: *"Staff may see their own record; never others'"*. G6
    had no key for that, so this route was `member.manage` and staff saw **nothing** — their own
    record included. Fail-closed and stricter than the spec, recorded as U6.

    **The grant lets them in; the query layer decides what they get.** `staff.view_own` is `ALLOW`
    for all three roles, and `query_scope._personnel_criteria` filters `Staff` to
    `user_id == current_user`, so a staff member reaching this page sees exactly one row — their
    own — while an owner sees the directory. Narrowing at the query layer rather than in the route
    is §9.4 step 4's rule, and it means `_ctx`'s `notes_map` inherits the filter for free: it
    iterates the already-filtered `staff` list, so it cannot build an entry for a colleague.

    The write routes below stay `member.manage`. Reading your own record and editing the roster
    are different powers, which is the whole reason row 10 needed splitting.
    """
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
    return with_toast(templates.TemplateResponse(request, "staff.html", _ctx(db)), f"{member.name} added")


@router.post("/{slug}/assign", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def assign_property(
    request: Request,
    slug: str,
    property_id: str = Form(...),
    db: Session = Depends(get_db),
):
    staff_svc.assign_to_property(db, slug, str(property_id))
    return with_toast(templates.TemplateResponse(request, "staff.html", _ctx(db)), "Property assigned")


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
    return with_toast(templates.TemplateResponse(request, "staff.html", _ctx(db)), f"{member.name} saved")


@router.post("/{slug}/delete", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def delete_staff(request: Request, slug: str, db: Session = Depends(get_db)):
    staff_svc.delete_staff(db, slug)
    return with_toast(templates.TemplateResponse(request, "staff.html", _ctx(db)), "Person removed")


@router.post("/{slug}/notes", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    member = staff_svc.get_staff(db, slug)
    note_svc.add_note(db, f"staff:{member.id}", content)
    notes = note_svc.list_notes(db, f"staff:{member.id}")
    return with_toast(templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/staff/{slug}/notes",
        "delete_url_prefix": f"/staff/{slug}/notes",
    }), "Note added")


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def delete_note(request: Request, slug: str, note_id: UUID, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    member = staff_svc.get_staff(db, slug)
    notes = note_svc.list_notes(db, f"staff:{member.id}")
    return with_toast(templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/staff/{slug}/notes",
        "delete_url_prefix": f"/staff/{slug}/notes",
    }), "Note deleted")


def _docs_ctx(db: Session, member, slug: str) -> dict:
    return {
        "docs": doc_svc.list_documents(db, entity_type="staff", entity_id=member.id),
        "post_url": f"/staff/{slug}/documents",
        "delete_url_prefix": f"/staff/{slug}/documents",
    }


@router.post("/{slug}/documents", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
async def add_staff_document(
    request: Request,
    slug: str,
    title: str = Form(...),
    doc_type: str = Form("other"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    member = staff_svc.get_staff(db, slug)
    try:
        file_path = await read_document_upload(file)
    except ValueError as e:
        ctx = _docs_ctx(db, member, slug)
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "partials/docs_section.html", ctx)
    doc_svc.create_document(
        db, title=title, file_path=file_path,
        document_type=DocumentType(doc_type),
        entity_type="staff", entity_id=member.id,
    )
    return with_toast(templates.TemplateResponse(
        request, "partials/docs_section.html", _docs_ctx(db, member, slug)), "Document added")


@router.delete("/{slug}/documents/{doc_id}", response_class=HTMLResponse)
@declares("member.manage", Access.ACCOUNT)
def delete_staff_document(request: Request, slug: str, doc_id: str, db: Session = Depends(get_db)):
    member = staff_svc.get_staff(db, slug)
    doc = doc_svc.get_document(db, doc_id)
    # The URL names a person; refuse to delete a document attached to anything else.
    if doc.entity_type != "staff" or doc.entity_id != member.id:
        raise HTTPException(status_code=404, detail="Document not found")
    doc_svc.delete_document(db, doc_id)
    return with_toast(templates.TemplateResponse(
        request, "partials/docs_section.html", _docs_ctx(db, member, slug)), "Document deleted")
