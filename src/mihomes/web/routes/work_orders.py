"""Work Orders routes."""

from datetime import datetime, date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.services import work_order as wo_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import vendor as vendor_svc
from mihomes.services import staff as staff_svc
from mihomes.web.deps import get_db, templates

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
        "staff": staff_svc.list_staff(db),
        "notes_map": {wo.id: note_svc.list_notes(db, f"workorder:{wo.id}") for wo in work_orders},
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
    db: Session = Depends(get_db),
):
    resolved_vendor_slug = vendor_slug
    if vendor_slug == "other" and vendor_name_other.strip():
        new_vendor = vendor_svc.create_vendor(db, vendor_name_other.strip())
        resolved_vendor_slug = new_vendor.slug
    wo_svc.create_work_order(
        db,
        title=title,
        property_id_or_slug=property_slug,
        description=description or None,
        vendor_id_or_slug=resolved_vendor_slug or None,
        estimated_cost=float(estimated_cost) if estimated_cost else None,
    )
    return templates.TemplateResponse(request, "work_orders.html", _ctx(db))


@router.post("/{slug}/generate-report", response_class=HTMLResponse)
def generate_report(request: Request, slug: str, db: Session = Depends(get_db)):
    wo = wo_svc.get_work_order(db, slug)
    try:
        from mihomes.services.ai.orchestrator import ask
        prompt = (
            f"Generate a detailed estate manager report for work order: '{wo.title}'.\n"
            f"Property: {wo.property.name if wo.property else 'Unknown'}\n"
            f"Status: {wo.status.value}\n"
            f"Description: {wo.description or 'None provided'}\n"
            f"Vendor: {wo.vendor.company_name if wo.vendor else 'None assigned'}\n"
            f"Estimated cost: {('$' + '{:,.2f}'.format(wo.estimated_cost)) if wo.estimated_cost else 'N/A'}\n"
            f"Actual cost: {('$' + '{:,.2f}'.format(wo.actual_cost)) if wo.actual_cost else 'N/A'}\n"
            f"Completion notes: {wo.completion_notes or 'None'}\n\n"
            "Write a thorough report covering: work scope, vendor performance, cost analysis, "
            "quality of work, any issues encountered, and recommendations for the homeowner. "
            "Be specific and professional."
        )
        response = ask(db, prompt, role="maintenance")
        wo.ai_report = response.text
        db.flush()
    except Exception as e:
        wo.ai_report = f"[Report generation failed: {e}]"
        db.flush()
    return templates.TemplateResponse(request, "partials/wo_report.html", {"wo": wo})


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
    db: Session = Depends(get_db),
):
    kwargs = dict(
        title=title,
        description=description or None,
        completion_notes=completion_notes or None,
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
        from mihomes.services.slug import resolve_identifier
        from mihomes.models.vendor import Vendor
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
