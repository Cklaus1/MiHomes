"""Zone service — CRUD for generalized property zones."""

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.space import Space
from mihomes.models.task import Task, TaskStatus
from mihomes.models.zone import Zone
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import (
    ensure_unique_slug,
    generate_slug,
    resolve_identifier,
)
from mihomes.services.update_helpers import safe_update


def create_zone(
    session: Session,
    name: str,
    property_id_or_slug: str,
    *,
    description: str | None = None,
    slug: str | None = None,
) -> Zone:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    slug = ensure_unique_slug(session, Zone, slug or generate_slug(name))
    zone = Zone(name=name, slug=slug, property_id=prop.id, description=description)
    session.add(zone)
    session.flush()
    record_change(session, "zone", zone.id, "create", snapshot_instance(zone))
    return zone


def list_zones(session: Session, property_id_or_slug: str) -> list[Zone]:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    return session.query(Zone).filter(Zone.property_id == prop.id).order_by(Zone.name).all()


def get_zone(session: Session, id_or_slug: str) -> Zone:
    return resolve_identifier(session, Zone, id_or_slug)


def update_zone(session: Session, id_or_slug: str, **kwargs) -> Zone:
    zone = resolve_identifier(session, Zone, id_or_slug)
    old_snap = snapshot_instance(zone)
    if "name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Zone, generate_slug(kwargs["name"]), exclude_id=zone.id)
    safe_update(zone, kwargs)
    session.flush()
    changes = diff_instance(old_snap, snapshot_instance(zone))
    if changes:
        record_change(session, "zone", zone.id, "update", changes)
    return zone


def delete_zone(session: Session, id_or_slug: str) -> str:
    zone = resolve_identifier(session, Zone, id_or_slug)
    # Unlink spaces and tasks before deleting
    session.query(Space).filter(Space.zone_id == zone.id).update({"zone_id": None})
    session.query(Task).filter(Task.zone_id == zone.id).update({"zone_id": None})
    name = zone.name
    record_change(session, "zone", zone.id, "delete", snapshot_instance(zone))
    session.delete(zone)
    session.flush()
    return name


def assign_space_to_zone(session: Session, space_id_or_slug: str, zone_id_or_slug: str) -> Space:
    space = resolve_identifier(session, Space, space_id_or_slug)
    zone = resolve_identifier(session, Zone, zone_id_or_slug)
    old_snap = snapshot_instance(space)
    space.zone_id = zone.id
    session.flush()
    changes = diff_instance(old_snap, snapshot_instance(space))
    if changes:
        record_change(session, "space", space.id, "update", changes)
    return space


def list_tasks_for_zone(session: Session, id_or_slug: str, open_only: bool = True) -> list[Task]:
    zone = resolve_identifier(session, Zone, id_or_slug)
    query = session.query(Task).filter(Task.zone_id == zone.id)
    if open_only:
        query = query.filter(Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]))
    return query.order_by(Task.due_date.asc().nullslast()).all()
