"""Contracts routes."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.document import DocumentType
from mihomes.services import contract as contract_svc
from mihomes.services import document as doc_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import vendor as vendor_svc
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import read_document_upload

router = APIRouter()


def _ctx(db: Session, auto_renew: bool | None = None) -> dict:
    all_contracts = contract_svc.list_contracts(db)
    contracts = [c for c in all_contracts if c.auto_renew] if auto_renew else all_contracts
    return {
        "page": "contracts",
        "all_contracts": all_contracts,
        "contracts": contracts,
        "properties": prop_svc.list_properties(db),
        "vendors": vendor_svc.list_vendors(db),
        "notes_map": {c.id: note_svc.list_notes(db, f"contract:{c.id}") for c in contracts},
        "docs_map": {c.id: doc_svc.list_documents(db, entity_type="contract", entity_id=c.id) for c in contracts},
        "filter_auto_renew": auto_renew,
        "billing_frequencies": [
            ("monthly", "Monthly"),
            ("bimonthly", "Bimonthly (every 2 months)"),
            ("quarterly", "Quarterly (every 3 months)"),
            ("semi-annual", "Semi-Annual (every 6 months)"),
            ("annual", "Annual"),
            ("one-time", "One-Time (fixed term)"),
        ],
    }


@router.get("/")
def list_contracts(request: Request, auto_renew: bool | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "contracts.html", _ctx(db, auto_renew))


@router.post("/", response_class=HTMLResponse)
def create_contract(
    request: Request,
    vendor_slug: str = Form(...),
    property_slug: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(""),
    service_category: str = Form(""),
    billing_frequency: str = Form(""),
    cost: str = Form(""),
    auto_renew: str = Form(""),
    notice_period_days: str = Form("30"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    contract_svc.create_contract(
        db,
        vendor_id_or_slug=vendor_slug,
        property_id_or_slug=property_slug,
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date) if end_date else None,
        service_category=service_category or None,
        billing_frequency=billing_frequency or None,
        cost=float(cost) if cost else None,
        auto_renew=bool(auto_renew),
        notice_period_days=int(notice_period_days) if notice_period_days else 30,
        notes=notes or None,
    )
    return templates.TemplateResponse(request, "contracts.html", _ctx(db))


@router.post("/{contract_id}/notes", response_class=HTMLResponse)
def add_note(request: Request, contract_id: int, content: str = Form(...), db: Session = Depends(get_db)):
    note_svc.add_note(db, f"contract:{contract_id}", content)
    notes = note_svc.list_notes(db, f"contract:{contract_id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/contracts/{contract_id}/notes",
        "delete_url_prefix": f"/contracts/{contract_id}/notes",
    })


@router.delete("/{contract_id}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, contract_id: int, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    notes = note_svc.list_notes(db, f"contract:{contract_id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/contracts/{contract_id}/notes",
        "delete_url_prefix": f"/contracts/{contract_id}/notes",
    })


@router.post("/{contract_id}/documents", response_class=HTMLResponse)
async def add_document(
    request: Request,
    contract_id: int,
    title: str = Form(...),
    doc_type: str = Form("other"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    ctx = {
        "post_url": f"/contracts/{contract_id}/documents",
        "delete_url_prefix": f"/contracts/{contract_id}/documents",
    }
    try:
        file_path = await read_document_upload(file)
    except ValueError as e:
        ctx["docs"] = doc_svc.list_documents(db, entity_type="contract", entity_id=contract_id)
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "partials/docs_section.html", ctx)
    doc_svc.create_document(
        db, title=title, file_path=file_path,
        document_type=DocumentType(doc_type),
        entity_type="contract", entity_id=contract_id,
    )
    ctx["docs"] = doc_svc.list_documents(db, entity_type="contract", entity_id=contract_id)
    return templates.TemplateResponse(request, "partials/docs_section.html", ctx)


@router.delete("/{contract_id}/documents/{doc_id}", response_class=HTMLResponse)
def delete_document(request: Request, contract_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc_svc.delete_document(db, str(doc_id))
    docs = doc_svc.list_documents(db, entity_type="contract", entity_id=contract_id)
    return templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/contracts/{contract_id}/documents",
        "delete_url_prefix": f"/contracts/{contract_id}/documents",
    })


@router.post("/{contract_id}/edit", response_class=HTMLResponse)
def edit_contract(
    request: Request,
    contract_id: int,
    notes: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    billing_frequency: str = Form(""),
    cost: str = Form(""),
    notice_period_days: str = Form(""),
    auto_renew: str = Form(""),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type
    kwargs = dict(notes=notes or None)
    if start_date: kwargs["start_date"] = date_type.fromisoformat(start_date)
    if end_date: kwargs["end_date"] = date_type.fromisoformat(end_date)
    kwargs["billing_frequency"] = billing_frequency or None
    if cost: kwargs["cost"] = float(cost)
    if notice_period_days: kwargs["notice_period_days"] = int(notice_period_days)
    kwargs["auto_renew"] = auto_renew == "1"
    contract_svc.update_contract(db, contract_id, **kwargs)
    return templates.TemplateResponse(request, "contracts.html", _ctx(db))


@router.post("/{contract_id}/delete", response_class=HTMLResponse)
def delete_contract(request: Request, contract_id: int, db: Session = Depends(get_db)):
    contract_svc.delete_contract(db, contract_id)
    return templates.TemplateResponse(request, "contracts.html", _ctx(db))
