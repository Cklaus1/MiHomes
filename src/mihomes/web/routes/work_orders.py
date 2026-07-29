"""Work Orders routes."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.document import DocumentType
from mihomes.services import document as doc_svc
from mihomes.services import issue as issue_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc
from mihomes.services import work_order as wo_svc
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import read_document_upload

router = APIRouter()


def _ctx(db: Session, status: str | None = None) -> dict:
    all_work_orders = wo_svc.list_work_orders(db)
    work_orders = [wo for wo in all_work_orders if wo.status.value == status] if status else all_work_orders
    return {
        "page": "work_orders",
        "all_work_orders": all_work_orders,
        "work_orders": work_orders,
        "properties": prop_svc.list_properties(db),
        "vendors": vendor_svc.list_vendors(db),
        "staff": staff_svc.list_staff(db, category="Staff"),
        "notes_map": {wo.id: note_svc.list_notes(db, f"workorder:{wo.id}") for wo in work_orders},
        "docs_map": {wo.id: doc_svc.list_documents(db, entity_type="work_order", entity_id=wo.id) for wo in work_orders},
        "issues": issue_svc.list_issues(db),
        "filter_status": status,
    }


@router.get("/")
def list_work_orders(request: Request, status: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db, status))


@router.post("/", response_class=HTMLResponse)
def create_work_order(
    request: Request,
    title: str = Form(...),
    property_slug: str = Form(...),
    description: str = Form(""),
    vendor_slug: str = Form(""),
    vendor_name_other: str = Form(""),
    estimated_cost: str = Form(""),
    due_date: str = Form(""),
    issue_id: str = Form(""),
    db: Session = Depends(get_db),
):
    resolved_vendor_slug = vendor_slug
    if vendor_slug == "other" and vendor_name_other.strip():
        new_vendor = vendor_svc.create_vendor(db, vendor_name_other.strip())
        resolved_vendor_slug = new_vendor.slug
    wo = wo_svc.create_work_order(
        db,
        title=title,
        property_id_or_slug=property_slug,
        description=description or None,
        vendor_id_or_slug=resolved_vendor_slug or None,
        estimated_cost=float(estimated_cost) if estimated_cost else None,
        due_date=date.fromisoformat(due_date) if due_date else None,
    )
    if issue_id:
        wo_svc.update_work_order(db, wo.slug, issue_id=int(issue_id))
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/notes", response_class=HTMLResponse)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    note_svc.add_note(db, f"workorder:{slug}", content)
    wo = wo_svc.get_work_order(db, slug)
    notes = note_svc.list_notes(db, f"workorder:{wo.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/work-orders/{slug}/notes",
        "delete_url_prefix": f"/work-orders/{slug}/notes",
    })


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, slug: str, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    wo = wo_svc.get_work_order(db, slug)
    notes = note_svc.list_notes(db, f"workorder:{wo.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/work-orders/{slug}/notes",
        "delete_url_prefix": f"/work-orders/{slug}/notes",
    })


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_work_order(
    request: Request,
    slug: str,
    title: str = Form(...),
    description: str = Form(""),
    completion_notes: str = Form(""),
    estimated_cost: str = Form(""),
    actual_cost: str = Form(""),
    due_date: str = Form(""),
    status: str = Form(""),
    vendor_slug: str = Form(""),
    vendor_name_other: str = Form(""),
    issue_id: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs = dict(
        title=title,
        description=description or None,
        completion_notes=completion_notes or None,
        issue_id=int(issue_id) if issue_id else None,
    )
    if estimated_cost:
        kwargs["estimated_cost"] = float(estimated_cost)
    if actual_cost:
        kwargs["actual_cost"] = float(actual_cost)
    if due_date:
        kwargs["due_date"] = date.fromisoformat(due_date)
    if status:
        from mihomes.models.work_order import WorkOrderStatus
        kwargs["status"] = WorkOrderStatus(status)
    if vendor_slug == "other" and vendor_name_other.strip():
        new_vendor = vendor_svc.create_vendor(db, vendor_name_other.strip())
        kwargs["vendor_id"] = new_vendor.id
    elif vendor_slug and vendor_slug != "other":
        from mihomes.models.vendor import Vendor
        from mihomes.services.slug import resolve_identifier
        v = resolve_identifier(db, Vendor, vendor_slug)
        kwargs["vendor_id"] = v.id
    elif vendor_slug == "":
        kwargs["vendor_id"] = None
    wo_svc.update_work_order(db, slug, **kwargs)
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    wo_svc.delete_work_order(db, slug)
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/approve", response_class=HTMLResponse)
def approve_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    wo_svc.approve(db, slug)
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/start", response_class=HTMLResponse)
def start_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    from mihomes.models.work_order import WorkOrderStatus
    wo_svc.transition_status(db, slug, WorkOrderStatus.IN_PROGRESS)
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/complete", response_class=HTMLResponse)
def complete_work_order(
    request: Request,
    slug: str,
    actual_cost: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        wo_svc.complete(
            db,
            slug,
            actual_cost=float(actual_cost) if actual_cost else None,
            notes=notes or None,
        )
    except ValueError:
        pass
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/verify", response_class=HTMLResponse)
def verify_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    wo_svc.verify(db, slug)
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/cancel", response_class=HTMLResponse)
def cancel_work_order(
    request: Request,
    slug: str,
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    wo_svc.cancel(db, slug, notes=notes or None)
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/documents", response_class=HTMLResponse)
async def add_document(
    request: Request,
    slug: str,
    title: str = Form(...),
    doc_type: str = Form("invoice"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    wo = wo_svc.get_work_order(db, slug)
    ctx = {
        "post_url": f"/work-orders/{slug}/documents",
        "delete_url_prefix": f"/work-orders/{slug}/documents",
    }
    try:
        file_path = await read_document_upload(file)
    except ValueError as e:
        ctx["docs"] = doc_svc.list_documents(db, entity_type="work_order", entity_id=wo.id)
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "partials/docs_section.html", ctx)
    doc_svc.create_document(
        db, title=title, file_path=file_path,
        document_type=DocumentType(doc_type),
        entity_type="work_order", entity_id=wo.id,
    )
    ctx["docs"] = doc_svc.list_documents(db, entity_type="work_order", entity_id=wo.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", ctx)


@router.delete("/{slug}/documents/{doc_id}", response_class=HTMLResponse)
def delete_document(request: Request, slug: str, doc_id: int, db: Session = Depends(get_db)):
    doc_svc.delete_document(db, str(doc_id))
    wo = wo_svc.get_work_order(db, slug)
    docs = doc_svc.list_documents(db, entity_type="work_order", entity_id=wo.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/work-orders/{slug}/documents",
        "delete_url_prefix": f"/work-orders/{slug}/documents",
    })
