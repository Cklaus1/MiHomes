"""Issue routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.issue import IssueSeverity, IssueStatus
from mihomes.services import issue as issue_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def list_issues(
    request: Request,
    property_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    issues = issue_svc.list_issues(db, property_id=property_id, status=status)
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(
        "issues.html",
        {
            "request": request,
            "page": "issues",
            "issues": issues,
            "properties": properties,
            "filter_property": property_id,
            "filter_status": status,
            "severities": [s.value for s in IssueSeverity],
            "statuses": [s.value for s in IssueStatus],
        },
    )


@router.post("/", response_class=HTMLResponse)
def create_issue(
    request: Request,
    title: str = Form(...),
    property_id: int = Form(...),
    severity: str = Form("medium"),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    issue_svc.create_issue(
        db,
        title=title,
        property_id=property_id,
        severity=IssueSeverity(severity),
        description=description or None,
    )
    issues = issue_svc.list_issues(db)
    return templates.TemplateResponse(
        "partials/issue_list.html",
        {"request": request, "issues": issues},
    )


@router.post("/{slug}/resolve", response_class=HTMLResponse)
def resolve_issue(request: Request, slug: str, db: Session = Depends(get_db)):
    issue = issue_svc.resolve_issue(db, slug)
    return templates.TemplateResponse(
        "partials/issue_row.html",
        {"request": request, "issue": issue},
    )
