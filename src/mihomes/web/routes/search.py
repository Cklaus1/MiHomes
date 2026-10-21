"""Search route."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.services import search as search_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()

ENTITY_TYPES = [
    ("", "All"),
    ("property", "Properties"),
    ("task", "Tasks"),
    ("issue", "Issues"),
    ("staff", "People"),
    ("vendor", "Vendors"),
    ("asset", "Assets"),
    ("workorder", "Work Orders"),
    ("document", "Documents"),
    ("note", "Notes"),
]

#: Where a result goes. `{slug}`/`{id}` are filled per result. Records that live in a modal on
#: their list page deep-link with `?open=`, so a search lands on the record, not on the list.
ENTITY_URLS = {
    "property": "/properties/{slug}/",
    "task": "/tasks/?open={id}",
    "issue": "/issues/?open={id}",
    "staff": "/staff/?open={id}",
    "resident": "/staff/?open={id}",
    "associate": "/staff/?open={id}",
    "family": "/staff/?open={id}",
    "vendor": "/vendors/?open={id}",
    "asset": "/assets/",
    "workorder": "/work-orders/?open={id}",
    "document": "/documents/?open={slug}",
    "note": None,
    "event": "/calendar/",
    "guest": None,
}


def result_url(item: dict) -> str | None:
    """The link for one search result, or None when it has nowhere to go."""
    template = ENTITY_URLS.get(item.get("type"))
    if not template:
        return None
    if "{slug}" in template and not item.get("slug"):
        return None
    return template.replace("{slug}", str(item.get("slug") or "")).replace(
        "{id}", str(item.get("id") or "")
    )


@router.get("/dropdown")
@declares("property.view", Access.COLLECTION)
def search_dropdown(request: Request, q: str = "", db: Session = Depends(get_db)):
    results = []
    if q.strip():
        results = search_svc.global_search(db, q.strip())[:8]
    return templates.TemplateResponse(request, "partials/search_results_dropdown.html", {
        "q": q,
        "results": results,
        "result_url": result_url,
    })


@router.get("/")
@declares("property.view", Access.COLLECTION)
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
        "result_url": result_url,
        "total": sum(len(v) for v in results_by_type.values()),
    })
