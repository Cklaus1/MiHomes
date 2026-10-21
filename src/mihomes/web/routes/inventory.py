"""Consumable inventory routes.

**Post/redirect/get.** The page's forms are plain `method="post"` forms, and each save used to
answer by rendering the page directly — so the browser's refresh re-submitted the last stock
count or price entry. Every successful save now answers 303 to `/inventory/` with the same
filters, plus `saved=<key>` for the confirmation banner. Validation errors are *not* redirected:
they re-render with 400, the error shown and (for Add Item) the modal reopened with the input kept.
"""

from datetime import date
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.consumable import Consumable, ConsumableStatus
from mihomes.services import property as prop_svc
from mihomes.services.consumable import (
    add_price_entry,
    create_consumable,
    delete_consumable,
    delete_price_entry,
    edit_price_entry,
    list_consumables,
    mark_ordered,
    mark_restocked,
    update_consumable,
    update_consumable_settings,
    update_stock,
)
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import parse_money

router = APIRouter()

# `saved` keys → banner text. A fixed table, never the query string itself, so a crafted link
# cannot make the page announce arbitrary text.
_SAVED = {
    "added": "Item added",
    "edited": "Item saved",
    "deleted": "Item deleted",
    "stock": "Stock updated",
    "settings": "Stock thresholds saved",
    "ordered": "Marked as ordered",
    "restocked": "Marked as restocked",
    "price": "Price history updated",
}


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


def _done(property_slug: str, category: str, saved: str) -> RedirectResponse:
    params = {k: v for k, v in (("property_slug", property_slug), ("category", category),
                                ("saved", saved)) if v}
    return RedirectResponse("/inventory/?" + urlencode(params), status_code=303)


def _invalid(request: Request, db: Session, property_slug: str, category: str, message: str,
             **extra) -> HTMLResponse:
    ctx = _ctx(db, property_slug or None, category or None)
    ctx["error"] = message
    ctx.update(extra)
    return templates.TemplateResponse(request, "inventory.html", ctx, status_code=400)


@router.post("/", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
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
        # Re-open the Add Item modal with what the user typed, rather than an empty form.
        return _invalid(
            request, db, filter_property, filter_category, str(e),
            open_modal="add-item-modal",
            add_form={
                "name": name, "property_slug": property_slug, "category": category, "unit": unit,
                "par_level": par_level, "quantity_in_stock": quantity_in_stock,
                "unit_price": unit_price, "low_stock_threshold": low_stock_threshold,
            },
        )
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
    return _done(filter_property, filter_category, "added")


@router.get("/")
@declares("inventory.manage", Access.COLLECTION)
def inventory_index(
    request: Request,
    property_slug: str | None = None,
    category: str | None = None,
    saved: str | None = None,
    db: Session = Depends(get_db),
):
    ctx = _ctx(db, property_slug, category)
    ctx["saved_message"] = _SAVED.get(saved or "")
    return templates.TemplateResponse(request, "inventory.html", ctx)


@router.post("/{slug}/edit", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
def edit_item(
    request: Request,
    slug: str,
    name: str = Form(...),
    category: str = Form(""),
    unit: str = Form(""),
    property_slug: str = Form(""),
    filter_category: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        update_consumable(db, slug, name=name, category=category.strip() or None, unit=unit.strip() or None)
    except ValueError as e:
        return _invalid(request, db, property_slug, filter_category, str(e))
    db.commit()
    return _done(property_slug, filter_category, "edited")


@router.post("/{slug}/delete", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
def delete_item(
    slug: str,
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    delete_consumable(db, slug)
    db.commit()
    return _done(property_slug, category, "deleted")


@router.post("/{slug}/stock", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
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
        try:
            qty = parse_money(quantity, "Quantity")
        except ValueError as e:
            return _invalid(request, db, property_slug, category, str(e))
        update_stock(db, item.name, str(item.property_id),
                     quantity_in_stock=qty)
        db.commit()
    return _done(property_slug, category, "stock")


@router.post("/{slug}/settings", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
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
        return _invalid(request, db, property_slug, category, str(e))
    update_consumable_settings(db, slug, par_level=par_level_val, low_stock_threshold=low_stock_threshold_val)
    db.commit()
    return _done(property_slug, category, "settings")


@router.post("/{slug}/ordered", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
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
    try:
        qty = parse_money(quantity_ordered, "Quantity ordered")
        price_val = parse_money(price, "Price")
        when = date.fromisoformat(order_date) if order_date else None
    except ValueError as e:
        return _invalid(request, db, property_slug, category, str(e))
    mark_ordered(
        db, slug,
        order_date=when,
        quantity_ordered=qty,
        price=price_val,
        note=note or None,
    )
    db.commit()
    return _done(property_slug, category, "ordered")


@router.post("/{slug}/restock", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
def restock(
    request: Request,
    slug: str,
    quantity: str = Form(""),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        qty = parse_money(quantity, "Quantity")
    except ValueError as e:
        return _invalid(request, db, property_slug, category, str(e))
    mark_restocked(db, slug, quantity=qty)
    db.commit()
    return _done(property_slug, category, "restocked")


@router.post("/{slug}/price-entries/{entry_id}/delete", response_class=HTMLResponse)
@declares("finance.view", Access.ITEM)
def delete_item_price(
    request: Request,
    slug: str,
    entry_id: UUID,
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    delete_price_entry(db, entry_id)
    db.commit()
    return _done(property_slug, category, "price")


def _parse_entry(price: str, entry_date: str, quantity: str) -> tuple[float, date, float]:
    price_val = parse_money(price, "Price")
    if price_val is None:
        raise ValueError("Price is required.")
    try:
        when = date.fromisoformat(entry_date)
    except ValueError:
        raise ValueError("Date must be a valid date.") from None
    qty = parse_money(quantity, "Quantity")
    return price_val, when, qty if qty else 1.0


@router.post("/{slug}/price-entries/{entry_id}/edit", response_class=HTMLResponse)
@declares("finance.view", Access.ITEM)
def edit_item_price(
    request: Request,
    slug: str,
    entry_id: UUID,
    price: str = Form(...),
    entry_date: str = Form(...),
    quantity: str = Form("1"),
    entry_type: str = Form("purchase"),
    note: str = Form(""),
    property_slug: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        price_val, when, qty = _parse_entry(price, entry_date, quantity)
    except ValueError as e:
        return _invalid(request, db, property_slug, category, str(e))
    edit_price_entry(
        db,
        entry_id,
        price_val,
        when,
        quantity=qty,
        entry_type=entry_type,
        note=note or None,
    )
    db.commit()
    return _done(property_slug, category, "price")


@router.post("/{slug}/price-entries", response_class=HTMLResponse)
@declares("finance.view", Access.ITEM)
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
    try:
        price_val, when, qty = _parse_entry(price, entry_date, quantity)
    except ValueError as e:
        return _invalid(request, db, property_slug, category, str(e))
    add_price_entry(
        db,
        slug,
        price_val,
        when,
        quantity=qty,
        entry_type=entry_type,
        note=note or None,
    )
    db.commit()
    return _done(property_slug, category, "price")
