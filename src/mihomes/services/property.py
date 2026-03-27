"""Property service — CRUD operations with audit logging."""

from datetime import date

from sqlalchemy.orm import Session

from mihomes.models.property import Property, PropertyStatus, PropertyType
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier


def create_property(
    session: Session,
    name: str,
    *,
    address: str | None = None,
    property_type: PropertyType = PropertyType.OTHER,
    status: PropertyStatus = PropertyStatus.OPEN,
    climate_zone: str | None = None,
    sqft: int | None = None,
    features: str | None = None,
    currency: str = "USD",
    slug: str | None = None,
) -> Property:
    slug = ensure_unique_slug(session, Property, slug or generate_slug(name))
    prop = Property(
        name=name,
        slug=slug,
        address=address,
        property_type=property_type,
        status=status,
        climate_zone=climate_zone,
        sqft=sqft,
        features=features,
        currency=currency,
    )
    session.add(prop)
    session.flush()
    record_change(session, "property", prop.id, "create", snapshot_instance(prop))
    return prop


def list_properties(
    session: Session,
    *,
    status: PropertyStatus | None = None,
    property_type: PropertyType | None = None,
) -> list[Property]:
    query = session.query(Property)
    if status is not None:
        query = query.filter(Property.status == status)
    if property_type is not None:
        query = query.filter(Property.property_type == property_type)
    return query.order_by(Property.name).all()


def get_property(session: Session, id_or_slug: str) -> Property:
    return resolve_identifier(session, Property, id_or_slug)


def update_property(session: Session, id_or_slug: str, **kwargs) -> Property:
    prop = resolve_identifier(session, Property, id_or_slug)
    old_snap = snapshot_instance(prop)

    # Handle slug change if name changes
    if "name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(
            session, Property, generate_slug(kwargs["name"]), exclude_id=prop.id
        )

    for key, value in kwargs.items():
        if hasattr(prop, key):
            setattr(prop, key, value)

    session.flush()
    new_snap = snapshot_instance(prop)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "property", prop.id, "update", changes)
    return prop


def delete_property(session: Session, id_or_slug: str) -> str:
    prop = resolve_identifier(session, Property, id_or_slug)
    name = prop.name
    record_change(session, "property", prop.id, "delete", snapshot_instance(prop))
    session.delete(prop)
    session.flush()
    return name


def occupy_property(
    session: Session,
    id_or_slug: str,
    from_date: date | None = None,
    until_date: date | None = None,
) -> Property:
    return update_property(
        session,
        id_or_slug,
        occupied=True,
        occupied_since=from_date or date.today(),
        occupied_until=until_date,
    )


def vacate_property(session: Session, id_or_slug: str) -> Property:
    return update_property(
        session,
        id_or_slug,
        occupied=False,
        occupied_since=None,
        occupied_until=None,
    )
