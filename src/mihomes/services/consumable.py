"""Consumable inventory service — stock tracking and reorder management."""

from sqlalchemy.orm import Session, joinedload

from mihomes.models.consumable import Consumable, ConsumableStatus
from mihomes.models.property import Property
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.validators import validate_name


def _compute_status(quantity_in_stock: float | None, par_level: float | None) -> ConsumableStatus:
    if quantity_in_stock is None:
        return ConsumableStatus.OK
    if quantity_in_stock <= 0:
        return ConsumableStatus.OUT
    if par_level is not None and quantity_in_stock <= par_level:
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
    existing = (
        session.query(Consumable)
        .filter(
            Consumable.property_id == prop.id,
            Consumable.name.ilike(name),
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
    notes: str | None = None,
) -> Consumable:
    name = validate_name(name, "consumable")
    prop = resolve_identifier(session, Property, property_id_or_slug)
    slug = ensure_unique_slug(session, Consumable, generate_slug(name))
    status = _compute_status(quantity_in_stock, par_level)
    item = Consumable(
        name=name,
        slug=slug,
        property_id=prop.id,
        unit=unit,
        category=category,
        par_level=par_level,
        quantity_in_stock=quantity_in_stock,
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
        item.status = _compute_status(quantity_in_stock, item.par_level)
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
    q = session.query(Consumable).options(joinedload(Consumable.property))
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        q = q.filter(Consumable.property_id == prop.id)
    if needs_reorder:
        q = q.filter(
            Consumable.status.in_([ConsumableStatus.LOW, ConsumableStatus.OUT])
        )
    return q.order_by(Consumable.status, Consumable.name).all()


def mark_ordered(session: Session, id_or_slug: str) -> Consumable:
    item = resolve_identifier(session, Consumable, id_or_slug)
    item.status = ConsumableStatus.ORDERED
    item.quantity_to_order = None
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
    item.status = _compute_status(item.quantity_in_stock, item.par_level)
    session.flush()
    return item


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
