"""Consumable inventory routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.consumable import Consumable, ConsumableStatus
from mihomes.services import property as prop_svc
from mihomes.services.consumable import list_consumables, update_stock, mark_ordered, mark_restocked
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session, property_slug: str | None = None, category: str | None = None) -> dict:
    properties = prop_svc.list_properties(db)
    items = list_consumables(db, property_id_or_slug=property_slug)
    if category:
        items = [i for i in items if i.category == category]

    # Group by category, preserve order
    groups: dict[str, list] = {}
    for item in items:
        cat = item.category or "General"
        groups.setdefault(cat, []).append(item)

    all_categories = list(dict.fromkeys(
        i.category or "General"
        for i in list_consumables(db, property_id_or_slug=property_slug)
    ))

    low_count = sum(1 for i in items if i.status in (ConsumableStatus.LOW, ConsumableStatus.OUT))

    return {
        "page": "inventory",
        "properties": properties,
        "groups": groups,
        "all_categories": all_categories,
        "filter_property": property_slug,
        "filter_category": category,
        "total": len(items),
        "low_count": low_count,
    }


@router.get("/")
def inventory_index(
    request: Request,
    property_slug: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "inventory.html", _ctx(db, property_slug, category))


@router.post("/{slug}/stock", response_class=HTMLResponse)
def set_stock(
    request: Request,
    slug: str,
    quantity: str = Form(...),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    item = db.query(Consumable).filter(Consumable.slug == slug).first()
    if item:
        qty = float(quantity) if quantity.strip() else None
        update_stock(db, item.name, str(item.property_id),
                     quantity_in_stock=qty)
        db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, property_slug or None, category or None))


@router.post("/{slug}/ordered", response_class=HTMLResponse)
def set_ordered(
    request: Request,
    slug: str,
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    mark_ordered(db, slug)
    db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, property_slug or None, category or None))


@router.post("/{slug}/restock", response_class=HTMLResponse)
def restock(
    request: Request,
    slug: str,
    quantity: str = Form(""),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    qty = float(quantity) if quantity.strip() else None
    mark_restocked(db, slug, quantity=qty)
    db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, property_slug or None, category or None))
