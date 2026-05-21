"""Vendor routes."""

from typing import List

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.services import note as note_svc
from mihomes.services import vendor as vendor_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session) -> dict:
    active_vendors = vendor_svc.list_vendors(db, active_only=True)
    all_vendors = vendor_svc.list_vendors(db, active_only=False)
    inactive_vendors = [v for v in all_vendors if not v.active]
    return {
        "page": "vendors",
        "active_vendors": active_vendors,
        "inactive_vendors": inactive_vendors,
        "vendor_ratings": {v.slug: vendor_svc.get_vendor_ratings(db, v.slug)["ratings"] for v in all_vendors},
        "notes_map": {v.id: note_svc.list_notes(db, f"vendor:{v.id}") for v in all_vendors},
    }


@router.get("/")
def list_vendors(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/", response_class=HTMLResponse)
def create_vendor(
    request: Request,
    company_name: str = Form(...),
    service_type: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    vendor_svc.create_vendor(
        db,
        company_name=company_name,
        phone=phone or None,
        email=email or None,
        service_categories=[service_type] if service_type else None,
    )
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/{slug}/rate", response_class=HTMLResponse)
def rate_vendor(
    request: Request,
    slug: str,
    quality: int = Form(...),
    reliability: int = Form(...),
    cost: int = Form(None),
    communication: int = Form(None),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    vendor_svc.rate_vendor(
        db,
        slug,
        quality=quality,
        reliability=reliability,
        cost=cost or None,
        communication=communication or None,
        notes=notes or None,
    )
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_vendor(
    request: Request,
    slug: str,
    company_name: str = Form(...),
    notes: str = Form(""),
    active: str | None = Form(None),
    service_categories_text: str = Form(""),
    website: str = Form(""),
    license_number: str = Form(""),
    c_name: List[str] = Form(default=[]),
    c_role: List[str] = Form(default=[]),
    c_phone: List[str] = Form(default=[]),
    c_email: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    current = vendor_svc.get_vendor(db, slug)
    active_val = (active == "1") if active is not None else current.active

    # Build contacts list — skip entirely empty rows
    contacts = []
    for name, role, phone, email in zip(c_name, c_role, c_phone, c_email):
        if any([name.strip(), phone.strip(), email.strip()]):
            contacts.append({
                "name": name.strip(),
                "role": role.strip(),
                "phone": phone.strip(),
                "email": email.strip(),
            })

    # Parse service categories from comma-separated text
    categories = [c.strip() for c in service_categories_text.split(",") if c.strip()] or None

    vendor_svc.update_vendor(
        db, slug,
        company_name=company_name,
        notes=notes or None,
        active=active_val,
        contacts=contacts or None,
        service_categories=categories,
        website=website.strip() or None,
        license_number=license_number.strip() or None,
    )
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_vendor(request: Request, slug: str, db: Session = Depends(get_db)):
    vendor_svc.delete_vendor(db, slug)
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/{slug}/notes", response_class=HTMLResponse)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    vendor = vendor_svc.get_vendor(db, slug)
    note_svc.add_note(db, f"vendor:{vendor.id}", content)
    notes = note_svc.list_notes(db, f"vendor:{vendor.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/vendors/{slug}/notes",
        "delete_url_prefix": f"/vendors/{slug}/notes",
    })


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, slug: str, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    vendor = vendor_svc.get_vendor(db, slug)
    notes = note_svc.list_notes(db, f"vendor:{vendor.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/vendors/{slug}/notes",
        "delete_url_prefix": f"/vendors/{slug}/notes",
    })
