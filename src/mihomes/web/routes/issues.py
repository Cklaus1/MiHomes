"""Issue routes."""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.document import DocumentType
from mihomes.models.issue import IssueSeverity, IssueStatus
from mihomes.services import document as doc_svc
from mihomes.services import issue as issue_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import work_order as wo_svc
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import read_document_upload

router = APIRouter()

_RESOLVED_STATUSES = {IssueStatus.RESOLVED, IssueStatus.VERIFIED}


def _ctx(db: Session, property_id=None, active_tab: str = "current") -> dict:
    issues = issue_svc.list_issues(
        db,
        property_id_or_slug=str(property_id) if property_id else None,
    )
    open_issues = [i for i in issues if i.status not in _RESOLVED_STATUSES]
    resolved_issues = [i for i in issues if i.status in _RESOLVED_STATUSES]
    return {
        "page": "issues",
        "open_issues": open_issues,
        "resolved_issues": resolved_issues,
        "properties": prop_svc.list_properties(db),
        "spaces": {p.id: space_svc.list_spaces(db, str(p.id)) for p in prop_svc.list_properties(db)},
        "staff": staff_svc.list_staff(db),
        "severities": [s.value for s in IssueSeverity],
        "statuses": [s.value for s in IssueStatus],
        "notes_map": {i.id: note_svc.list_notes(db, f"issue:{i.id}") for i in issues},
        "filter_property": property_id,
        "active_tab": active_tab,
        "wo_map": {i.id: wo_svc.list_work_orders_by_issue(db, i.id) for i in issues},
        "docs_map": {i.id: doc_svc.list_documents(db, entity_type="issue", entity_id=i.id) for i in issues},
    }


@router.get("/")
def list_issues(
    request: Request,
    property_id: int | None = None,
    tab: str = "current",
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "issues.html", _ctx(db, property_id, active_tab=tab))


@router.post("/", response_class=HTMLResponse)
def create_issue(
    request: Request,
    title: str = Form(...),
    property_id: int = Form(...),
    severity: str = Form("medium"),
    description: str = Form(""),
    reported_by_id: str | None = Form(None),
    space_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    issue_svc.create_issue(
        db,
        title=title,
        property_id_or_slug=str(property_id),
        severity=IssueSeverity(severity),
        description=description or None,
        reported_by_id=int(reported_by_id) if reported_by_id else None,
        space_id_or_slug=space_id or None,
    )
    return templates.TemplateResponse(request, "issues.html", _ctx(db, active_tab="current"))


@router.post("/{slug}/resolve", response_class=HTMLResponse)
def resolve_issue(
    request: Request,
    slug: str,
    resolved_by_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    issue_svc.resolve_issue(db, slug, resolved_by_id=int(resolved_by_id) if resolved_by_id else None)
    return templates.TemplateResponse(request, "issues.html", _ctx(db, active_tab="current"))


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_issue(
    request: Request,
    slug: str,
    title: str = Form(...),
    severity: str = Form("medium"),
    description: str = Form(""),
    status: str = Form(""),
    reported_by_id: str | None = Form(None),
    resolved_by_id: str | None = Form(None),
    space_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    kwargs = dict(
        title=title,
        severity=IssueSeverity(severity),
        description=description or None,
        reported_by_id=int(reported_by_id) if reported_by_id else None,
        space_id=int(space_id) if space_id else None,
    )
    if status:
        kwargs["status"] = IssueStatus(status)
    if resolved_by_id:
        kwargs["resolved_by_id"] = int(resolved_by_id)
    issue_svc.update_issue(db, slug, **kwargs)
    new_status = kwargs.get("status")
    tab = "history" if new_status in _RESOLVED_STATUSES else "current"
    return templates.TemplateResponse(request, "issues.html", _ctx(db, active_tab=tab))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_issue(request: Request, slug: str, db: Session = Depends(get_db)):
    issue_svc.delete_issue(db, slug)
    return templates.TemplateResponse(request, "issues.html", _ctx(db, active_tab="current"))


@router.post("/{slug}/notes", response_class=HTMLResponse)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    issue = issue_svc.get_issue(db, slug)
    note_svc.add_note(db, f"issue:{issue.id}", content)
    notes = note_svc.list_notes(db, f"issue:{issue.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/issues/{slug}/notes",
        "delete_url_prefix": f"/issues/{slug}/notes",
    })


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, slug: str, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    issue = issue_svc.get_issue(db, slug)
    notes = note_svc.list_notes(db, f"issue:{issue.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/issues/{slug}/notes",
        "delete_url_prefix": f"/issues/{slug}/notes",
    })


@router.post("/{slug}/documents", response_class=HTMLResponse)
async def add_document(
    request: Request,
    slug: str,
    title: str = Form(...),
    doc_type: str = Form("other"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    issue = issue_svc.get_issue(db, slug)
    ctx = {
        "post_url": f"/issues/{slug}/documents",
        "delete_url_prefix": f"/issues/{slug}/documents",
    }
    try:
        file_path = await read_document_upload(file)
    except ValueError as e:
        ctx["docs"] = doc_svc.list_documents(db, entity_type="issue", entity_id=issue.id)
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "partials/docs_section.html", ctx)
    doc_svc.create_document(
        db, title=title, file_path=file_path,
        document_type=DocumentType(doc_type),
        entity_type="issue", entity_id=issue.id,
    )
    ctx["docs"] = doc_svc.list_documents(db, entity_type="issue", entity_id=issue.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", ctx)


@router.delete("/{slug}/documents/{doc_id}", response_class=HTMLResponse)
def delete_document(request: Request, slug: str, doc_id: int, db: Session = Depends(get_db)):
    doc_svc.delete_document(db, str(doc_id))
    issue = issue_svc.get_issue(db, slug)
    docs = doc_svc.list_documents(db, entity_type="issue", entity_id=issue.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/issues/{slug}/documents",
        "delete_url_prefix": f"/issues/{slug}/documents",
    })
