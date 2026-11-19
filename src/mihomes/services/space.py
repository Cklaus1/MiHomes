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


def content_counts(session: Session, property_id_or_slug: str) -> dict:
    """{space_id: {"assets": n, "books": n}} for one property's rooms.

    Feeds the room-delete confirmation ("its 23 assets and 70 books will be kept"), which is
    rendered from three pages; one grouped query each rather than a count per room.
    """
    from sqlalchemy import func

    from mihomes.models.asset import Asset
    from mihomes.models.book import Book

    prop = resolve_identifier(session, Property, property_id_or_slug)
    counts: dict = {}
    for model, key in ((Asset, "assets"), (Book, "books")):
        rows = (
            session.query(model.space_id, func.count(model.id))
            .filter(model.property_id == prop.id, model.space_id.isnot(None))
            .group_by(model.space_id)
            .all()
        )
        for space_id, n in rows:
            counts.setdefault(space_id, {"assets": 0, "books": 0})[key] = n
    return counts


def delete_space(session: Session, id_or_slug: str) -> str:
    """Delete a room, keeping everything in it.

    Assets, books and issues reference `spaces.id` with no ON DELETE rule, so deleting a room
    that still holds anything used to fail with a foreign-key error. Nothing in a room is lost
    when the room goes: each row is unassigned (space_id → NULL) and audit-logged first.
    """
    from mihomes.models.asset import Asset
    from mihomes.models.book import Book
    from mihomes.models.issue import Issue

    space = resolve_identifier(session, Space, id_or_slug)
    name = space.name
    for model, entity in ((Asset, "asset"), (Book, "book"), (Issue, "issue")):
        for row in session.query(model).filter(model.space_id == space.id).all():
            row.space_id = None
            record_change(session, entity, row.id, "update",
                          {"space_id": {"old": str(space.id), "new": None}})
    session.flush()
    record_change(session, "space", space.id, "delete", snapshot_instance(space))
    session.delete(space)
    session.flush()
    return name
