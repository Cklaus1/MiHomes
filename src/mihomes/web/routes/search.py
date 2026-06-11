"""Search route."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from mihomes.services import search as search_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()

ENTITY_TYPES = [
    ("", "All"),
    ("property", "Properties"),
    ("task", "Tasks"),
    ("issue", "Issues"),
    ("staff", "Staff"),
    ("vendor", "Vendors"),
    ("asset", "Assets"),
    ("workorder", "Work Orders"),
    ("document", "Documents"),
    ("note", "Notes"),
]

ENTITY_URLS = {
    "property": "/properties/{slug}/",
    "task": "/tasks/",
    "issue": "/issues/",
    "staff": "/staff/",
    "resident": "/staff/",
    "associate": "/staff/",
    "family": "/staff/",
    "vendor": "/vendors/",
    "asset": "/assets/",
    "workorder": "/work-orders/",
    "document": None,
    "note": None,
    "event": None,
    "guest": None,
}


@router.get("/")
def search(
    request: Request,
    q: str = "",
    type: str = "",
    db: Session = Depends(get_db),
):
    results_by_type: dict[str, list] = {}
    if q.strip():
        raw = search_svc.global_search(db, q.strip(), entity_type=type or None)
        for r in raw:
            results_by_type.setdefault(r["type"], []).append(r)

    return templates.TemplateResponse(request, "search.html", {
        "page": "search",
        "q": q,
        "type_filter": type,
        "results_by_type": results_by_type,
        "entity_types": ENTITY_TYPES,
        "entity_urls": ENTITY_URLS,
        "total": sum(len(v) for v in results_by_type.values()),
    })
