"""Work Orders routes."""

from datetime import date
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.document import DocumentType
from mihomes.models.issue import IssueStatus
from mihomes.models.staff import Staff
from mihomes.models.work_order import WorkOrderStatus
from mihomes.services import document as doc_svc
from mihomes.services import issue as issue_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc
from mihomes.services import work_order as wo_svc
from mihomes.services.slug import EntityNotFoundError, resolve_identifier
from mihomes.web.deps import get_db, templates, with_toast
from mihomes.web.forms import read_document_upload

router = APIRouter()

_RESOLVED_ISSUE = {IssueStatus.RESOLVED, IssueStatus.VERIFIED}


def _ctx(
    db: Session,
    status: str | None = None,
    *,
    open_id=None,
    open_new: bool = False,
    prefill_issue=None,
) -> dict:
    all_work_orders = wo_svc.list_work_orders(db)
    work_orders = [wo for wo in all_work_orders if wo.status.value == status] if status else all_work_orders
    # Modals are rendered for the listed rows — plus the one being reopened after a lifecycle
    # action, which a status filter may now hide (approving a WO under "Draft" moves it out).
    modal_work_orders = list(work_orders)
    if open_id and not any(str(wo.id) == str(open_id) for wo in modal_work_orders):
        modal_work_orders += [wo for wo in all_work_orders if str(wo.id) == str(open_id)]
    return {
        "page": "work_orders",
        "all_work_orders": all_work_orders,
        "work_orders": work_orders,
        "modal_work_orders": modal_work_orders,
        "properties": prop_svc.list_properties(db),
        "vendors": vendor_svc.list_vendors(db),
        "staff": staff_svc.list_staff(db, category="Staff"),
        "notes_map": {wo.id: note_svc.list_notes(db, f"workorder:{wo.id}") for wo in modal_work_orders},
        "docs_map": {wo.id: doc_svc.list_documents(db, entity_type="work_order", entity_id=wo.id) for wo in modal_work_orders},
        # Linking a work order to an issue that is already fixed is never what anyone means; the
        # edit form adds back a WO's own (resolved) issue so saving it does not unlink it.
        "issues": [i for i in issue_svc.list_issues(db) if i.status not in _RESOLVED_ISSUE],
        "filter_status": status,
        # Free plans cannot set a due date (`work_order.schedule`); the field renders disabled.
        "wo_scheduling_entitled": wo_svc.scheduling_is_entitled(db),
        # One-shot: open this WO's modal (?open=<id>, or after a lifecycle action) / the add modal.
        "open_id": str(open_id) if open_id else None,
        "open_new": open_new or prefill_issue is not None,
        "prefill_issue": prefill_issue,
    }


def _current_status(request: Request) -> str | None:
    """The status filter the user was looking at, from `HX-Current-URL` (a body swap keeps it)."""
    current = urlsplit(request.headers.get("hx-current-url", ""))
    if not current.path.startswith("/work-orders"):
        return None                          # posted from another page: its query isn't ours
    return (parse_qs(current.query).get("status") or [""])[0].strip() or None


def _page(request: Request, db: Session, toast: str | None = None, *, open_id=None, error=None):
    """The work-orders page as the user had it filtered, optionally reopening one record."""
    ctx = _ctx(db, _current_status(request), open_id=open_id)
    if error:
        ctx["error"] = error
    resp = templates.TemplateResponse(request, "work_orders.html", ctx)
    return with_toast(resp, toast) if toast else resp


#: The edit form's "Unassigned" option. Not "": FastAPI turns an empty optional form value into
#: None, which would be indistinguishable from "this form has no assignee field at all".
UNASSIGNED = "none"


def _assignee_id(db: Session, value: str | None):
    """A posted assignee: blank/"none" is unassigned; anything else must be a staff member."""
    value = (value or "").strip()
    if not value or value == UNASSIGNED:
        return None
    return resolve_identifier(db, Staff, value).id


def _promote_if_assigned(db: Session, wo) -> None:
    """An approved work order that has someone on it is `assigned` — the step the UI had no
    way to reach. Uses the state machine, so nothing else can be skipped this way."""
    if wo.status == WorkOrderStatus.APPROVED and wo.assignee_id:
        wo_svc.transition_status(db, str(wo.id), WorkOrderStatus.ASSIGNED)


@router.get("/")
@declares("issue.manage", Access.COLLECTION)
def list_work_orders(
    request: Request,
    status: str | None = None,
    open: str | None = None,
    new: bool = False,
    issue: str | None = None,
    db: Session = Depends(get_db),
):
    # `?new=1&issue=<id>` is "Create work order" from an issue: the add form opens prefilled.
    prefill_issue = None
    if new and (issue or "").strip():
        try:
            prefill_issue = issue_svc.get_issue(db, issue.strip())
        except EntityNotFoundError:
            prefill_issue = None
    return templates.TemplateResponse(
        request, "work_orders.html",
        _ctx(db, status, open_id=(open or "").strip() or None, open_new=new,
             prefill_issue=prefill_issue),
    )


@router.post("/", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
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
    assignee_id: str = Form(""),
    db: Session = Depends(get_db),
):
    resolved_vendor_slug = vendor_slug
    if vendor_slug == "other" and vendor_name_other.strip():
        new_vendor = vendor_svc.create_vendor(db, vendor_name_other.strip())
        resolved_vendor_slug = new_vendor.slug
    elif vendor_slug == "other":
        resolved_vendor_slug = ""
    wo = wo_svc.create_work_order(
        db,
        title=title,
        property_id_or_slug=property_slug,
        description=description or None,
        vendor_id_or_slug=resolved_vendor_slug or None,
        assignee_id_or_slug=assignee_id.strip() or None,
        estimated_cost=float(estimated_cost) if estimated_cost else None,
        due_date=date.fromisoformat(due_date) if due_date else None,
    )
    if issue_id:
        wo_svc.update_work_order(db, wo.slug, issue_id=issue_id)
    return _page(request, db, "Work order created")


@router.post("/{slug}/notes", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    note_svc.add_note(db, f"workorder:{slug}", content)
    wo = wo_svc.get_work_order(db, slug)
    notes = note_svc.list_notes(db, f"workorder:{wo.id}")
    return with_toast(templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/work-orders/{slug}/notes",
        "delete_url_prefix": f"/work-orders/{slug}/notes",
    }), "Note added")


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def delete_note(request: Request, slug: str, note_id: UUID, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    wo = wo_svc.get_work_order(db, slug)
    notes = note_svc.list_notes(db, f"workorder:{wo.id}")
    return with_toast(templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/work-orders/{slug}/notes",
        "delete_url_prefix": f"/work-orders/{slug}/notes",
    }), "Note deleted")


@router.post("/{slug}/edit", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
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
    assignee_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    before = wo_svc.get_work_order(db, slug)
    prev_status, prev_assignee = before.status, before.assignee_id
    kwargs = dict(
        title=title,
        description=description or None,
        completion_notes=completion_notes or None,
        issue_id=issue_id or None,
    )
    if estimated_cost:
        kwargs["estimated_cost"] = float(estimated_cost)
    if actual_cost:
        kwargs["actual_cost"] = float(actual_cost)
    if due_date:
        kwargs["due_date"] = date.fromisoformat(due_date)
    if status:
        kwargs["status"] = WorkOrderStatus(status)
    # Only when the form sends the field — a caller posting without it must not unassign.
    if assignee_id is not None:
        kwargs["assignee_id"] = _assignee_id(db, assignee_id)
    if vendor_slug == "other" and vendor_name_other.strip():
        new_vendor = vendor_svc.create_vendor(db, vendor_name_other.strip())
        kwargs["vendor_id"] = new_vendor.id
    elif vendor_slug and vendor_slug != "other":
        from mihomes.models.vendor import Vendor
        v = resolve_identifier(db, Vendor, vendor_slug)
        kwargs["vendor_id"] = v.id
    elif vendor_slug == "":
        kwargs["vendor_id"] = None
    wo = wo_svc.update_work_order(db, slug, **kwargs)
    # Giving an approved work order an assignee moves it to Assigned — unless the user changed
    # the status select themselves, in which case their choice stands.
    status_untouched = not status or WorkOrderStatus(status) == prev_status
    if status_untouched and prev_status == WorkOrderStatus.APPROVED and wo.assignee_id != prev_assignee:
        _promote_if_assigned(db, wo)
    return _page(request, db, "Work order saved", open_id=wo.id)


@router.post("/{slug}/delete", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def delete_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    wo_svc.delete_work_order(db, slug)
    return _page(request, db, "Work order deleted")


# Lifecycle actions re-render with `open_id`: the modal stays open on the same record, so the
# user sees the new status and the next step instead of being dropped back on the list.
@router.post("/{slug}/approve", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def approve_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    wo = wo_svc.approve(db, slug)
    _promote_if_assigned(db, wo)
    return _page(request, db, "Work order approved", open_id=wo.id)


@router.post("/{slug}/start", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def start_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    wo = wo_svc.transition_status(db, slug, WorkOrderStatus.IN_PROGRESS)
    return _page(request, db, "Work order started", open_id=wo.id)


@router.post("/{slug}/complete", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def complete_work_order(
    request: Request,
    slug: str,
    actual_cost: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    wo = wo_svc.get_work_order(db, slug)
    wo_id = wo.id
    try:
        wo_svc.complete(
            db,
            slug,
            actual_cost=float(actual_cost) if actual_cost else None,
            notes=notes or None,
        )
    except ValueError as e:
        # H22: surface the validation error (e.g. missing cost) instead of
        # silently swallowing it — the work order stays un-completed and the
        # user sees why.
        db.rollback()
        return _page(request, db, open_id=wo_id, error=str(e))
    return _page(request, db, "Work order completed", open_id=wo_id)


@router.post("/{slug}/verify", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def verify_work_order(request: Request, slug: str, db: Session = Depends(get_db)):
    wo = wo_svc.verify(db, slug)
    return _page(request, db, "Work order verified", open_id=wo.id)


@router.post("/{slug}/cancel", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def cancel_work_order(
    request: Request,
    slug: str,
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    wo = wo_svc.cancel(db, slug, notes=notes or None)
    return _page(request, db, "Work order cancelled", open_id=wo.id)


@router.post("/{slug}/documents", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
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
    return with_toast(
        templates.TemplateResponse(request, "partials/docs_section.html", ctx), "Document uploaded"
    )


@router.delete("/{slug}/documents/{doc_id}", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
def delete_document(request: Request, slug: str, doc_id: str, db: Session = Depends(get_db)):
    doc_svc.delete_document(db, doc_id)
    wo = wo_svc.get_work_order(db, slug)
    docs = doc_svc.list_documents(db, entity_type="work_order", entity_id=wo.id)
    return with_toast(templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/work-orders/{slug}/documents",
        "delete_url_prefix": f"/work-orders/{slug}/documents",
    }), "Document deleted")
