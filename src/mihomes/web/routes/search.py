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
    ("staff", "Directory"),  # the sidebar's name for /staff/
    ("vendor", "Vendors"),
    ("asset", "Assets"),
    ("workorder", "Work Orders"),
    ("document", "Documents"),
    ("note", "Notes"),
]

#: Heading for each result group: the filter's names, plus types that have no filter chip.
TYPE_LABELS = {**{k: v for k, v in ENTITY_TYPES if k}, "event": "Events", "guest": "Guests"}

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
    # The asset itself, in its room (`?open=asset-<id>` is the Rooms & assets page's deep link),
    # rather than the Rooms & assets index it used to land on.
    "asset": "/assets/{property_slug}/{space_slug}?open=asset-{id}",
    "space": "/assets/{property_slug}/{slug}",
    "workorder": "/work-orders/?open={id}",
    "document": "/documents/?open={slug}",
    "event": "/calendar/?open=event-{id}",
    "contract": None,
    "guest": None,
}


def result_url(item: dict) -> str | None:
    """The link for one search result, or None when it has nowhere to go.

    A note has no page of its own: it links to the record it is on (`item["parent"]`, shaped
    like a result by the search service). Every `{field}` in a template must be filled, or the
    result is shown without a link rather than with a broken one.
    """
    if item.get("type") == "note":
        parent = item.get("parent")
        return result_url(parent) if parent else None
    template = ENTITY_URLS.get(item.get("type"))
    if not template:
        return None
    fields = {k: v for k, v in item.items() if v not in (None, "")}
    try:
        return template.format_map(fields)
    except KeyError:
        return None


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
        "type_labels": TYPE_LABELS,
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
        "type_labels": TYPE_LABELS,
        "total": sum(len(v) for v in results_by_type.values()),
    })
