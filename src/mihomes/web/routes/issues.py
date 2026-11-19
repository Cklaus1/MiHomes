"""Issue routes."""

from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.document import DocumentType
from mihomes.models.issue import IssueSeverity, IssueStatus
from mihomes.services import document as doc_svc
from mihomes.services import issue as issue_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import work_order as wo_svc
from mihomes.web.deps import get_db, templates, with_toast
from mihomes.web.forms import read_document_upload

router = APIRouter()

_RESOLVED_STATUSES = {IssueStatus.RESOLVED, IssueStatus.VERIFIED}
_SEVERITIES = {s.value for s in IssueSeverity}
_STATUSES = {s.value for s in IssueStatus}


def _ctx(
    db: Session,
    property_id=None,
    active_tab: str = "current",
    *,
    severity: str | None = None,
    status: str | None = None,
    open_id: str | None = None,
    open_new: bool = False,
) -> dict:
    severity = severity if severity in _SEVERITIES else None
    status = status if status in _STATUSES else None
    issues = issue_svc.list_issues(
        db,
        property_id_or_slug=str(property_id) if property_id else None,
        severity=IssueSeverity(severity) if severity else None,
    )
    if status:
        issues = [i for i in issues if i.status.value == status]
    open_issues = [i for i in issues if i.status not in _RESOLVED_STATUSES]
    resolved_issues = [i for i in issues if i.status in _RESOLVED_STATUSES]
    properties = prop_svc.list_properties(db)
    return {
        "page": "issues",
        "open_issues": open_issues,
        "resolved_issues": resolved_issues,
        "properties": properties,
        "spaces": {p.id: space_svc.list_spaces(db, str(p.id)) for p in properties},
        "staff": staff_svc.list_staff(db),
        "severities": [s.value for s in IssueSeverity],
        "statuses": [s.value for s in IssueStatus],
        "notes_map": {i.id: note_svc.list_notes(db, f"issue:{i.id}") for i in issues},
        # Filters as strings: ids compare against `{{ p.id|string }}` in the selects.
        "filter_property": str(property_id) if property_id else None,
        "filter_severity": severity,
        "filter_status": status,
        "active_tab": active_tab if active_tab in ("current", "history") else "current",
        "wo_map": {i.id: wo_svc.list_work_orders_by_issue(db, i.id) for i in issues},
        "docs_map": {i.id: doc_svc.list_documents(db, entity_type="issue", entity_id=i.id) for i in issues},
        # One-shot: open this issue's modal (?open=<id>) or the Report Issue modal (?new=1).
        "open_id": open_id,
        "open_new": open_new,
    }


def _page_filters(request: Request) -> dict:
    """The filters and tab the user was looking at, read from `HX-Current-URL`.

    Every save swaps the whole page into <body>; re-rendering with a bare `_ctx(db)` dropped the
    property filter and jumped back to the Current tab. The swap leaves the URL alone (and the
    tab buttons keep `?tab=` in it), so the URL still says what the user was looking at.
    `open`/`new` are one-shot and never carried.
    """
    current = urlsplit(request.headers.get("hx-current-url", ""))
    if not current.path.startswith("/issues"):
        return {}                            # e.g. Report Issue on the assets page
    query = parse_qs(current.query)

    def one(key):
        return (query.get(key) or [""])[0].strip() or None

    return {
        "property_id": one("property_id"),
        "active_tab": one("tab") or "current",
        "severity": one("severity"),
        "status": one("status"),
    }


def _page(request: Request, db: Session, toast: str | None = None, *, error: str | None = None,
          status_code: int = 200, **kw):
    """The issues page as the user had it filtered, plus an optional success toast."""
    ctx = _ctx(db, **{**_page_filters(request), **kw})
    if error:
        ctx["error"] = error
    resp = templates.TemplateResponse(request, "issues.html", ctx, status_code=status_code)
    return with_toast(resp, toast) if toast else resp


@router.get("/")
@declares("issue.manage", Access.COLLECTION)
def list_issues(
    request: Request,
    property_id: str | None = None,
    tab: str = "current",
    severity: str | None = None,
    status: str | None = None,
    open: str | None = None,
    new: bool = False,
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "issues.html",
        _ctx(db, (property_id or "").strip() or None, active_tab=tab,
             severity=severity, status=status,
             open_id=(open or "").strip() or None, open_new=new),
    )


@router.post("/", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def create_issue(
    request: Request,
    title: str = Form(...),
    property_id: str = Form(...),
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
        reported_by_id=reported_by_id or None,
        space_id_or_slug=space_id or None,
    )
    return _page(request, db, "Issue reported", active_tab="current")


@router.post("/{slug}/resolve", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def resolve_issue(
    request: Request,
    slug: str,
    resolved_by_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        issue_svc.resolve_issue(db, slug, resolved_by_id=resolved_by_id or None)
    except ValueError as exc:
        db.rollback()
        return _page(request, db, error=str(exc), status_code=400)
    return _page(request, db, "Issue resolved")


@router.post("/{slug}/edit", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
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
        reported_by_id=reported_by_id or None,
        space_id=space_id or None,
    )
    if status:
        kwargs["status"] = IssueStatus(status)
    if resolved_by_id:
        kwargs["resolved_by_id"] = resolved_by_id
    issue_svc.update_issue(db, slug, **kwargs)
    # Follow the issue to the tab it now lives on; otherwise stay where the user was.
    new_status = kwargs.get("status")
    tab = {}
    if new_status is not None:
        tab = {"active_tab": "history" if new_status in _RESOLVED_STATUSES else "current"}
    return _page(request, db, "Issue saved", **tab)


@router.post("/{slug}/delete", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def delete_issue(request: Request, slug: str, db: Session = Depends(get_db)):
    issue_svc.delete_issue(db, slug)
    return _page(request, db, "Issue deleted")


@router.post("/{slug}/notes", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    issue = issue_svc.get_issue(db, slug)
    note_svc.add_note(db, f"issue:{issue.id}", content)
    notes = note_svc.list_notes(db, f"issue:{issue.id}")
    return with_toast(templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/issues/{slug}/notes",
        "delete_url_prefix": f"/issues/{slug}/notes",
    }), "Note added")


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def delete_note(request: Request, slug: str, note_id: UUID, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    issue = issue_svc.get_issue(db, slug)
    notes = note_svc.list_notes(db, f"issue:{issue.id}")
    return with_toast(templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/issues/{slug}/notes",
        "delete_url_prefix": f"/issues/{slug}/notes",
    }), "Note deleted")


@router.post("/{slug}/documents", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
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
    return with_toast(
        templates.TemplateResponse(request, "partials/docs_section.html", ctx), "Document uploaded"
    )


@router.delete("/{slug}/documents/{doc_id}", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
def delete_document(request: Request, slug: str, doc_id: str, db: Session = Depends(get_db)):
    doc_svc.delete_document(db, doc_id)
    issue = issue_svc.get_issue(db, slug)
    docs = doc_svc.list_documents(db, entity_type="issue", entity_id=issue.id)
    return with_toast(templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/issues/{slug}/documents",
        "delete_url_prefix": f"/issues/{slug}/documents",
    }), "Document deleted")
