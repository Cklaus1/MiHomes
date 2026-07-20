"""Documents & playbooks routes."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from mihomes.models.document import DocumentType
from mihomes.services import document as doc_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()

DOC_TYPE_LABELS = {
    "sop": "Playbook / SOP",
    "manual": "Manual",
    "warranty": "Warranty",
    "contract": "Contract",
    "insurance": "Insurance",
    "permit": "Permit",
    "regulation": "Regulation",
    "report": "Report",
    "other": "Other",
}

DOC_TYPE_COLORS = {
    "sop": "violet",
    "manual": "blue",
    "warranty": "emerald",
    "contract": "orange",
    "insurance": "sky",
    "permit": "amber",
    "regulation": "red",
    "report": "indigo",
    "other": "gray",
}


def _ctx(db: Session, type_filter: str = "", **kwargs) -> dict:
    dt = DocumentType(type_filter) if type_filter else None
    documents = doc_svc.list_documents(db, document_type=dt)
    expiring = doc_svc.list_expiring(db, days=60)
    expiring_ids = {d.id for d in expiring}
    return {
        "page": "documents",
        "documents": documents,
        "properties": prop_svc.list_properties(db),
        "doc_types": [(t.value, DOC_TYPE_LABELS.get(t.value, t.value.title())) for t in DocumentType],
        "doc_type_labels": DOC_TYPE_LABELS,
        "doc_type_colors": DOC_TYPE_COLORS,
        "type_filter": type_filter,
        "expiring_ids": expiring_ids,
        **kwargs,
    }


@router.get("/")
def list_documents(request: Request, type: str = "", db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "documents.html", _ctx(db, type_filter=type))


@router.post("/")
def create_document(
    request: Request,
    title: str = Form(...),
    document_type: str = Form(...),
    file_path: str = Form(""),
    notes: str | None = Form(None),
    expires_at: str | None = Form(None),
    property_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    doc_svc.create_document(
        db,
        title=title,
        file_path=file_path.strip() or "—",
        document_type=DocumentType(document_type),
        notes=notes or None,
        expires_at=date.fromisoformat(expires_at) if expires_at else None,
        entity_type="property" if property_id else None,
        entity_id=int(property_id) if property_id else None,
    )
    return templates.TemplateResponse(request, "documents.html", _ctx(db))


@router.post("/{slug}/edit")
def edit_document(
    request: Request,
    slug: str,
    title: str = Form(...),
    document_type: str = Form(...),
    file_path: str = Form(""),
    notes: str | None = Form(None),
    expires_at: str | None = Form(None),
    property_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    doc_svc.update_document(
        db, slug,
        title=title,
        document_type=DocumentType(document_type),
        file_path=file_path.strip() or "—",
        notes=notes or None,
        expires_at=date.fromisoformat(expires_at) if expires_at else None,
        entity_type="property" if property_id else None,
        entity_id=int(property_id) if property_id else None,
    )
    return templates.TemplateResponse(request, "documents.html", _ctx(db))


@router.post("/{slug}/delete")
def delete_document(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
):
    doc_svc.delete_document(db, slug)
    return templates.TemplateResponse(request, "documents.html", _ctx(db))
