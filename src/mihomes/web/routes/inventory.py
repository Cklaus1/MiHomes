"""Consumable inventory routes."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.consumable import Consumable, ConsumableStatus
from mihomes.services import property as prop_svc
from mihomes.services.consumable import (
    add_price_entry,
    create_consumable,
    delete_price_entry,
    edit_price_entry,
    list_consumables,
    mark_ordered,
    mark_restocked,
    update_consumable_settings,
    update_stock,
)
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import parse_money

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


@router.post("/", response_class=HTMLResponse)
def add_item(
    request: Request,
    name: str = Form(...),
    property_slug: str = Form(...),
    category: str = Form(""),
    unit: str = Form(""),
    par_level: str = Form(""),
    quantity_in_stock: str = Form(""),
    unit_price: str = Form(""),
    low_stock_threshold: str = Form(""),
    filter_property: str = Form(""),
    filter_category: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        par_level_val = parse_money(par_level, "Par level")
        quantity_val = parse_money(quantity_in_stock, "Quantity in stock")
        unit_price_val = parse_money(unit_price, "Unit price")
        low_stock_threshold_val = parse_money(low_stock_threshold, "Low-stock threshold")
    except ValueError as e:
        ctx = _ctx(db, filter_property or None, filter_category or None)
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "inventory.html", ctx)
    item = create_consumable(
        db,
        name=name,
        property_id_or_slug=property_slug,
        category=category or None,
        unit=unit or None,
        par_level=par_level_val,
        quantity_in_stock=quantity_val,
        low_stock_threshold=low_stock_threshold_val,
    )
    if unit_price_val is not None:
        add_price_entry(db, item.slug, unit_price_val, date.today(), entry_type="purchase")
    db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, filter_property or None, filter_category or None))


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


@router.post("/{slug}/settings", response_class=HTMLResponse)
def update_settings(
    request: Request,
    slug: str,
    par_level: str = Form(""),
    low_stock_threshold: str = Form(""),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        par_level_val = parse_money(par_level, "Par level")
        low_stock_threshold_val = parse_money(low_stock_threshold, "Low-stock threshold")
    except ValueError as e:
        ctx = _ctx(db, property_slug or None, category or None)
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "inventory.html", ctx)
    update_consumable_settings(db, slug, par_level=par_level_val, low_stock_threshold=low_stock_threshold_val)
    db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, property_slug or None, category or None))


@router.post("/{slug}/ordered", response_class=HTMLResponse)
def set_ordered(
    request: Request,
    slug: str,
    order_date: str = Form(""),
    quantity_ordered: str = Form(""),
    price: str = Form(""),
    note: str = Form(""),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    mark_ordered(
        db, slug,
        order_date=date.fromisoformat(order_date) if order_date else None,
        quantity_ordered=float(quantity_ordered) if quantity_ordered else None,
        price=float(price) if price else None,
        note=note or None,
    )
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


@router.post("/{slug}/price-entries/{entry_id}/delete", response_class=HTMLResponse)
def delete_item_price(
    request: Request,
    slug: str,
    entry_id: int,
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    delete_price_entry(db, entry_id)
    db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, property_slug or None, category or None))


@router.post("/{slug}/price-entries/{entry_id}/edit", response_class=HTMLResponse)
def edit_item_price(
    request: Request,
    slug: str,
    entry_id: int,
    price: str = Form(...),
    entry_date: str = Form(...),
    quantity: str = Form("1"),
    entry_type: str = Form("purchase"),
    note: str = Form(""),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    edit_price_entry(
        db,
        entry_id,
        float(price),
        date.fromisoformat(entry_date),
        quantity=float(quantity) if quantity else 1.0,
        entry_type=entry_type,
        note=note or None,
    )
    db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, property_slug or None, category or None))


@router.post("/{slug}/price-entries", response_class=HTMLResponse)
def add_item_price(
    request: Request,
    slug: str,
    price: str = Form(...),
    entry_date: str = Form(...),
    quantity: str = Form("1"),
    entry_type: str = Form("purchase"),
    note: str = Form(""),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    add_price_entry(
        db,
        slug,
        float(price),
        date.fromisoformat(entry_date),
        quantity=float(quantity) if quantity else 1.0,
        entry_type=entry_type,
        note=note or None,
    )
    db.commit()
    return templates.TemplateResponse(request, "inventory.html",
                                      _ctx(db, property_slug or None, category or None))
