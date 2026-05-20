"""Library route — all-properties book inventory listing."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from mihomes.services import book as book_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def library_index(request: Request, property_slug: str | None = None, db: Session = Depends(get_db)):
    properties = prop_svc.list_properties(db)
    books = book_svc.list_books(
        db,
        property_id_or_slug=property_slug or None,
        active_only=False,
    )

    # Attach property and space names for display
    from mihomes.models.property import Property
    from mihomes.models.space import Space
    prop_map = {p.id: p for p in properties}
    space_ids = {b.space_id for b in books if b.space_id}
    space_map = {}
    if space_ids:
        spaces = db.query(Space).filter(Space.id.in_(space_ids)).all()
        space_map = {s.id: s for s in spaces}

    enriched = []
    for b in books:
        enriched.append({
            "book": b,
            "property": prop_map.get(b.property_id),
            "space": space_map.get(b.space_id) if b.space_id else None,
        })

    return templates.TemplateResponse(request, "library.html", {
        "page": "library",
        "properties": properties,
        "books": enriched,
        "total": len(enriched),
        "filter_property": property_slug,
    })
