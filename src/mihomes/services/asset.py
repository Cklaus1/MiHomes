"""Asset service — CRUD and asset queries."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.models.asset import Asset, AssetCondition, AssetType
from mihomes.models.property import Property
from mihomes.models.space import Space
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier


def create_asset(
    session: Session,
    name: str,
    asset_type: AssetType,
    property_id_or_slug: str,
    *,
    space_id_or_slug: str | None = None,
    make: str | None = None,
    model_name: str | None = None,
    serial_number: str | None = None,
    purchase_date: date | None = None,
    purchase_price: float | None = None,
    warranty_expires: date | None = None,
    condition: AssetCondition = AssetCondition.GOOD,
    notes: str | None = None,
    vehicle_info: dict | None = None,
    valuable_info: dict | None = None,
    equipment_info: dict | None = None,
    slug: str | None = None,
) -> Asset:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    space_id = None
    if space_id_or_slug:
        space = resolve_identifier(session, Space, space_id_or_slug)
        space_id = space.id
    slug = ensure_unique_slug(session, Asset, slug or generate_slug(name))
    asset = Asset(
        name=name, slug=slug, asset_type=asset_type,
        property_id=prop.id, space_id=space_id,
        make=make, model_name=model_name, serial_number=serial_number,
        purchase_date=purchase_date, purchase_price=purchase_price,
        warranty_expires=warranty_expires, condition=condition, notes=notes,
        vehicle_info=vehicle_info, valuable_info=valuable_info,
        equipment_info=equipment_info,
    )
    session.add(asset)
    session.flush()
    record_change(session, "asset", asset.id, "create", snapshot_instance(asset))
    return asset


def list_assets(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    asset_type: AssetType | None = None,
    active_only: bool = True,
) -> list[Asset]:
    query = session.query(Asset)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Asset.property_id == prop.id)
    if asset_type:
        query = query.filter(Asset.asset_type == asset_type)
    if active_only:
        query = query.filter(Asset.active.is_(True))
    return query.order_by(Asset.name).all()


def get_asset(session: Session, id_or_slug: str) -> Asset:
    return resolve_identifier(session, Asset, id_or_slug)


def update_asset(session: Session, id_or_slug: str, **kwargs) -> Asset:
    asset = resolve_identifier(session, Asset, id_or_slug)
    old_snap = snapshot_instance(asset)
    if "name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Asset, generate_slug(kwargs["name"]), exclude_id=asset.id)
    safe_update(asset, kwargs)
    session.flush()
    new_snap = snapshot_instance(asset)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "asset", asset.id, "update", changes)
    return asset


def delete_asset(session: Session, id_or_slug: str) -> str:
    asset = resolve_identifier(session, Asset, id_or_slug)
    name = asset.name
    record_change(session, "asset", asset.id, "delete", snapshot_instance(asset))
    session.delete(asset)
    session.flush()
    return name


def list_by_warranty_expiring(session: Session, days: int = 30) -> list[Asset]:
    """List assets with warranties expiring within the given number of days."""
    cutoff = date.today() + timedelta(days=days)
    return session.query(Asset).filter(
        Asset.warranty_expires != None,  # noqa: E711
        Asset.warranty_expires <= cutoff,
        Asset.warranty_expires >= date.today(),
        Asset.active == True,  # noqa: E712
    ).order_by(Asset.warranty_expires).all()


def list_by_type(session: Session, asset_type: AssetType) -> list[Asset]:
    """List all active assets of a given type."""
    return session.query(Asset).filter(
        Asset.asset_type == asset_type,
        Asset.active == True,  # noqa: E712
    ).order_by(Asset.name).all()
