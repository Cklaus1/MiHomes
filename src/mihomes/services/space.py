"""Space service — CRUD for rooms/areas within properties."""

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.space import Space
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import (
    ensure_unique_slug,
    generate_slug,
    resolve_identifier,
)
from mihomes.services.update_helpers import safe_update
from mihomes.services.validators import validate_name


def create_space(
    session: Session,
    name: str,
    property_id_or_slug: str,
    *,
    space_type: str | None = None,
    description: str | None = None,
    slug: str | None = None,
) -> Space:
    name = validate_name(name, "space")
    prop = resolve_identifier(session, Property, property_id_or_slug)
    slug = ensure_unique_slug(session, Space, slug or generate_slug(name))
    space = Space(
        name=name,
        slug=slug,
        property_id=prop.id,
        space_type=space_type,
        description=description,
    )
    session.add(space)
    session.flush()
    record_change(session, "space", space.id, "create", snapshot_instance(space))
    return space


def list_spaces(session: Session, property_id_or_slug: str) -> list[Space]:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    return (
        session.query(Space)
        .filter(Space.property_id == prop.id)
        .order_by(Space.name)
        .all()
    )


def get_space(session: Session, id_or_slug: str) -> Space:
    return resolve_identifier(session, Space, id_or_slug)


def update_space(session: Session, id_or_slug: str, **kwargs) -> Space:
    space = resolve_identifier(session, Space, id_or_slug)
    old_snap = snapshot_instance(space)
    if "name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Space, generate_slug(kwargs["name"]), exclude_id=space.id)
    safe_update(space, kwargs)
    session.flush()
    changes = diff_instance(old_snap, snapshot_instance(space))
    if changes:
        record_change(session, "space", space.id, "update", changes)
    return space


def delete_space(session: Session, id_or_slug: str) -> str:
    space = resolve_identifier(session, Space, id_or_slug)
    name = space.name
    record_change(session, "space", space.id, "delete", snapshot_instance(space))
    session.delete(space)
    session.flush()
    return name
