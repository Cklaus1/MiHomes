"""Vendor routes."""

from pathlib import Path
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.document import DocumentType
from mihomes.services import document as document_svc
from mihomes.services import note as note_svc
from mihomes.services import vendor as vendor_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()
_UPLOADS_DIR = Path(__file__).parent.parent / "static" / "uploads"


def _ctx(db: Session) -> dict:
    from collections import Counter

    from mihomes.services import property as prop_svc

    active_vendors = vendor_svc.list_vendors(db, active_only=True)
    all_vendors = vendor_svc.list_vendors(db, active_only=False)
    inactive_vendors = [v for v in all_vendors if not v.active]

    properties = prop_svc.list_properties(db)
    prop_slug_by_id = {p.id: p.slug for p in properties}

    # Build vendor → [property_slug, ...] from vendor.property_ids (direct tagging)
    vendor_properties: dict[int, list[str]] = {
        v.id: [prop_slug_by_id[pid] for pid in (v.property_ids or []) if pid in prop_slug_by_id]
        for v in all_vendors
    }

    # Build case-normalized, deduplicated category list (most common casing wins)
    cat_groups: dict[str, Counter] = {}
    for v in all_vendors:
        for cat in (v.service_categories or []):
            cat = cat.strip()
            if cat:
                cat_groups.setdefault(cat.lower(), Counter())[cat] += 1
    all_categories = sorted(max(counts, key=counts.get) for counts in cat_groups.values())

    vendor_docs_map = {
        v.slug: document_svc.list_documents(db, entity_type="vendor", entity_id=v.id)
        for v in all_vendors
    }
    return {
        "page": "vendors",
        "active_vendors": active_vendors,
        "inactive_vendors": inactive_vendors,
        "vendor_ratings": {v.slug: vendor_svc.get_vendor_ratings(db, v.slug)["ratings"] for v in all_vendors},
        "notes_map": {v.id: note_svc.list_notes(db, f"vendor:{v.id}") for v in all_vendors},
        "vendor_docs_map": vendor_docs_map,
        "properties": properties,
        "vendor_properties": vendor_properties,
        "all_categories": all_categories,
        "service_categories": vendor_svc.SERVICE_CATEGORIES,
    }


@router.get("/")
def list_vendors(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/", response_class=HTMLResponse)
def create_vendor(
    request: Request,
    company_name: str = Form(...),
    service_cats: List[str] = Form(default=[]),
    service_cat_other: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    prop_ids: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    cats = list(service_cats)
    if service_cat_other.strip():
        cats.append(service_cat_other.strip())
    vendor_svc.create_vendor(
        db,
        company_name=company_name,
        phone=phone or None,
        email=email or None,
        service_categories=cats or None,
        property_ids=[int(p) for p in prop_ids if p.isdigit()] or None,
    )
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/categories/delete", response_class=HTMLResponse)
def delete_category(request: Request, category: str = Form(...), db: Session = Depends(get_db)):
    vendor_svc.delete_category(db, category)
    return templates.TemplateResponse(request, "vendors.html", _ctx(db))


@router.post("/categories/rename", response_class=HTMLResponse)
def rename_category(request: Request, old_name: str = Form(...), new_name: str = Form(...), db: Session = Depends(get_db)):
    vendor_svc.rename_category(db, old_name.strip(), new_name.strip())
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
    service_cats: List[str] = Form(default=[]),
    service_cat_other: str = Form(""),
    website: str = Form(""),
    license_number: str = Form(""),
    c_name: List[str] = Form(default=[]),
    c_role: List[str] = Form(default=[]),
    c_phone: List[str] = Form(default=[]),
    c_email: List[str] = Form(default=[]),
    prop_ids: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    current = vendor_svc.get_vendor(db, slug)
    active_val = (active == "1") if active is not None else current.active

    # Build contacts list — skip entirely empty rows
    contacts = []
    for name, role, phone, email in zip(c_name, c_role, c_phone, c_email, strict=True):
        if any([name.strip(), phone.strip(), email.strip()]):
            contacts.append({
                "name": name.strip(),
                "role": role.strip(),
                "phone": phone.strip(),
                "email": email.strip(),
            })

    cats = list(service_cats)
    if service_cat_other.strip():
        cats.append(service_cat_other.strip())
    categories = cats or None

    vendor_svc.update_vendor(
        db, slug,
        company_name=company_name,
        notes=notes or None,
        active=active_val,
        contacts=contacts or None,
        service_categories=categories,
        website=website.strip() or None,
        license_number=license_number.strip() or None,
        property_ids=[int(p) for p in prop_ids if p.isdigit()] or None,
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


@router.post("/{slug}/documents", response_class=HTMLResponse)
async def add_document(
    request: Request,
    slug: str,
    title: str = Form(...),
    doc_type: str = Form("invoice"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    vendor = vendor_svc.get_vendor(db, slug)
    suffix = Path(file.filename).suffix.lower()
    filename = f"{uuid4().hex}{suffix}"
    (_UPLOADS_DIR / filename).write_bytes(await file.read())
    document_svc.create_document(
        db,
        title=title,
        file_path=f"/static/uploads/{filename}",
        document_type=DocumentType(doc_type),
        entity_type="vendor",
        entity_id=vendor.id,
    )
    docs = document_svc.list_documents(db, entity_type="vendor", entity_id=vendor.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/vendors/{slug}/documents",
        "delete_url_prefix": f"/vendors/{slug}/documents",
    })


@router.delete("/{slug}/documents/{doc_id}", response_class=HTMLResponse)
def delete_document(request: Request, slug: str, doc_id: int, db: Session = Depends(get_db)):
    document_svc.delete_document(db, str(doc_id))
    vendor = vendor_svc.get_vendor(db, slug)
    docs = document_svc.list_documents(db, entity_type="vendor", entity_id=vendor.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/vendors/{slug}/documents",
        "delete_url_prefix": f"/vendors/{slug}/documents",
    })
