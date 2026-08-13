"""Consumable inventory service — stock tracking and reorder management."""

from datetime import date

from sqlalchemy.orm import Session, joinedload

from mihomes.models.consumable import Consumable, ConsumablePriceEntry, ConsumableStatus
from mihomes.models.property import Property
from mihomes.services.query_helpers import exact_ci
from mihomes.services.slug import ensure_unique_slug, generate_slug, get_by_id, resolve_identifier
from mihomes.services.validators import validate_name


def _compute_status(
    quantity_in_stock: float | None,
    par_level: float | None,
    low_stock_threshold: float | None = None,
) -> ConsumableStatus:
    """Determine stock status.

    Default urgency (no custom threshold set): a par level of 1 means there's
    no real "low" zone — it's fine until it's gone (OUT already covers that).
    A par level above 1 flags LOW once only 1 unit is left, regardless of how
    high the par level itself is — flagging LOW at, say, 19/20 in stock (the
    old `qty <= par_level` behavior) is a false alarm, not a real warning.
    """
    if quantity_in_stock is None:
        return ConsumableStatus.OK
    if quantity_in_stock <= 0:
        return ConsumableStatus.OUT
    if low_stock_threshold is not None:
        return ConsumableStatus.LOW if quantity_in_stock < low_stock_threshold else ConsumableStatus.OK
    if par_level is not None:
        if par_level == 1 and quantity_in_stock < 1:
            return ConsumableStatus.LOW
        if par_level > 1 and quantity_in_stock < 2:
            return ConsumableStatus.LOW
    return ConsumableStatus.OK


def get_or_create_consumable(
    session: Session,
    name: str,
    property_id_or_slug: str,
    *,
    unit: str | None = None,
    category: str | None = None,
) -> Consumable:
    """Find existing consumable by name+property, or create it."""
    prop = resolve_identifier(session, Property, property_id_or_slug)
    # M10: match the name exactly (case-insensitive). ilike(name) treated any
    # %/_ in the name as a wildcard and could match the wrong existing row.
    existing = (
        session.query(Consumable)
        .filter(
            Consumable.property_id == prop.id,
            exact_ci(Consumable.name, name),
        )
        .first()
    )
    if existing:
        return existing
    slug = ensure_unique_slug(session, Consumable, generate_slug(name))
    item = Consumable(
        name=name,
        slug=slug,
        property_id=prop.id,
        unit=unit,
        category=category,
    )
    session.add(item)
    session.flush()
    return item


def create_consumable(
    session: Session,
    name: str,
    property_id_or_slug: str,
    *,
    unit: str | None = None,
    category: str | None = None,
    par_level: float | None = None,
    quantity_in_stock: float | None = None,
    low_stock_threshold: float | None = None,
    notes: str | None = None,
) -> Consumable:
    name = validate_name(name, "consumable")
    prop = resolve_identifier(session, Property, property_id_or_slug)
    slug = ensure_unique_slug(session, Consumable, generate_slug(name))
    status = _compute_status(quantity_in_stock, par_level, low_stock_threshold)
    item = Consumable(
        name=name,
        slug=slug,
        property_id=prop.id,
        unit=unit,
        category=category,
        par_level=par_level,
        quantity_in_stock=quantity_in_stock,
        low_stock_threshold=low_stock_threshold,
        status=status,
        notes=notes,
    )
    session.add(item)
    session.flush()
    return item


def update_stock(
    session: Session,
    name_or_slug: str,
    property_id_or_slug: str,
    *,
    quantity_in_stock: float | None = None,
    quantity_to_order: float | None = None,
    unit: str | None = None,
    updated_by: str | None = None,
) -> Consumable:
    """Update stock or order quantity for a consumable. Creates if not found."""
    item = get_or_create_consumable(session, name_or_slug, property_id_or_slug, unit=unit)
    if quantity_in_stock is not None:
        item.quantity_in_stock = quantity_in_stock
        item.status = _compute_status(quantity_in_stock, item.par_level, item.low_stock_threshold)
    if quantity_to_order is not None:
        item.quantity_to_order = quantity_to_order
        if item.status == ConsumableStatus.OK and quantity_to_order > 0:
            item.status = ConsumableStatus.LOW
    if unit and not item.unit:
        item.unit = unit
    if updated_by:
        item.last_updated_by = updated_by
    session.flush()
    return item


def list_consumables(
    session: Session,
    property_id_or_slug: str | None = None,
    needs_reorder: bool = False,
) -> list[Consumable]:
    q = session.query(Consumable).options(joinedload(Consumable.property), joinedload(Consumable.price_entries))
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        q = q.filter(Consumable.property_id == prop.id)
    if needs_reorder:
        q = q.filter(
            Consumable.status.in_([ConsumableStatus.LOW, ConsumableStatus.OUT])
        )
    return q.order_by(Consumable.status, Consumable.name).all()


def mark_ordered(
    session: Session,
    id_or_slug: str,
    *,
    order_date: date | None = None,
    quantity_ordered: float | None = None,
    price: float | None = None,
    note: str | None = None,
) -> Consumable:
    item = resolve_identifier(session, Consumable, id_or_slug)
    item.status = ConsumableStatus.ORDERED
    item.quantity_to_order = None
    item.last_ordered_at = order_date or date.today()
    if price is not None:
        add_price_entry(
            session, id_or_slug, price, item.last_ordered_at,
            quantity=quantity_ordered or 1.0,
            entry_type="purchase",
            note=note,
        )
    session.flush()
    return item


def mark_restocked(
    session: Session,
    id_or_slug: str,
    quantity: float | None = None,
) -> Consumable:
    item = resolve_identifier(session, Consumable, id_or_slug)
    if quantity is not None:
        item.quantity_in_stock = quantity
    item.quantity_to_order = None
    item.status = _compute_status(item.quantity_in_stock, item.par_level, item.low_stock_threshold)
    session.flush()
    return item


def update_consumable_settings(
    session: Session,
    id_or_slug: str,
    *,
    par_level: float | None = None,
    low_stock_threshold: float | None = None,
) -> Consumable:
    """Update an item's par level and/or custom low-stock threshold, recomputing status."""
    item = resolve_identifier(session, Consumable, id_or_slug)
    item.par_level = par_level
    item.low_stock_threshold = low_stock_threshold
    item.status = _compute_status(item.quantity_in_stock, item.par_level, item.low_stock_threshold)
    session.flush()
    return item


def add_price_entry(
    session: Session,
    id_or_slug: str,
    price: float,
    entry_date: date,
    *,
    quantity: float = 1.0,
    entry_type: str = "purchase",
    note: str | None = None,
) -> ConsumablePriceEntry:
    item = resolve_identifier(session, Consumable, id_or_slug)
    entry = ConsumablePriceEntry(
        consumable_id=item.id,
        date=entry_date,
        price=price,
        quantity=quantity,
        entry_type=entry_type,
        note=note,
    )
    session.add(entry)
    item.unit_price = price
    session.flush()
    return entry


def delete_price_entry(session: Session, entry_id: int) -> None:
    entry = get_by_id(session, ConsumablePriceEntry, entry_id)
    if not entry:
        raise ValueError(f"Price entry {entry_id} not found")
    consumable_id = entry.consumable_id
    session.delete(entry)
    session.flush()
    # Sync unit_price to new latest entry (or None if none left)
    latest = (
        session.query(ConsumablePriceEntry)
        .filter(ConsumablePriceEntry.consumable_id == consumable_id)
        .order_by(ConsumablePriceEntry.date.desc())
        .first()
    )
    item = get_by_id(session, Consumable, consumable_id)
    if item:
        item.unit_price = latest.price if latest else None


def edit_price_entry(
    session: Session,
    entry_id: int,
    price: float,
    entry_date: date,
    *,
    quantity: float = 1.0,
    entry_type: str = "purchase",
    note: str | None = None,
) -> ConsumablePriceEntry:
    entry = get_by_id(session, ConsumablePriceEntry, entry_id)
    if not entry:
        raise ValueError(f"Price entry {entry_id} not found")
    entry.price = price
    entry.date = entry_date
    entry.quantity = quantity
    entry.entry_type = entry_type
    entry.note = note
    # Keep unit_price in sync with the most recent entry
    latest = (
        session.query(ConsumablePriceEntry)
        .filter(ConsumablePriceEntry.consumable_id == entry.consumable_id)
        .order_by(ConsumablePriceEntry.date.desc())
        .first()
    )
    if latest:
        entry.consumable.unit_price = latest.price
    session.flush()
    return entry


def get_reorder_list(
    session: Session,
    property_id_or_slug: str | None = None,
) -> list[Consumable]:
    """Items that are low, out, or have a pending order quantity."""
    q = session.query(Consumable).options(joinedload(Consumable.property))
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        q = q.filter(Consumable.property_id == prop.id)
    return q.filter(
        Consumable.status.in_([ConsumableStatus.LOW, ConsumableStatus.OUT])
        | Consumable.quantity_to_order.isnot(None)
    ).order_by(Consumable.status, Consumable.name).all()
