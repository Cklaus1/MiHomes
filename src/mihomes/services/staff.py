"""Staff service — CRUD and property assignment."""

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.staff import Staff, StaffRole, category_for_role
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.validators import validate_name


def create_staff(
    session: Session,
    name: str,
    *,
    role: StaffRole = StaffRole.OTHER,
    phone: str | None = None,
    email: str | None = None,
    whatsapp_phone: str | None = None,
    certifications: str | None = None,
    property_id_or_slug: str | None = None,
    slug: str | None = None,
) -> Staff:
    name = validate_name(name, "staff")
    slug = ensure_unique_slug(session, Staff, slug or generate_slug(name))
    member = Staff(
        name=name,
        slug=slug,
        role=role,
        phone=phone,
        email=email,
        whatsapp_phone=whatsapp_phone,
        certifications=certifications,
    )
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        member.properties.append(prop)
    session.add(member)
    session.flush()
    record_change(session, "staff", member.id, "create", snapshot_instance(member))
    return member


def list_staff(
    session: Session,
    *,
    role: StaffRole | None = None,
    category: str | None = None,
    active_only: bool = True,
) -> list[Staff]:
    query = session.query(Staff)
    if active_only:
        query = query.filter(Staff.active.is_(True))
    if role is not None:
        query = query.filter(Staff.role == role)
    rows = query.order_by(Staff.name).all()
    # `category` (Staff / Resident / Associate / Family / Owner) is derived from
    # role in Python, so filter after the query.
    if category is not None:
        rows = [r for r in rows if category_for_role(r.role) == category]
    return rows


def get_staff(session: Session, id_or_slug: str) -> Staff:
    return resolve_identifier(session, Staff, id_or_slug)


def update_staff(session: Session, id_or_slug: str, **kwargs) -> Staff:
    member = resolve_identifier(session, Staff, id_or_slug)
    old_snap = snapshot_instance(member)
    if "name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Staff, generate_slug(kwargs["name"]), exclude_id=member.id)
    safe_update(member, kwargs)
    session.flush()
    new_snap = snapshot_instance(member)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "staff", member.id, "update", changes)
    return member


def delete_staff(session: Session, id_or_slug: str) -> str:
    from mihomes.models.task import Task
    member = resolve_identifier(session, Staff, id_or_slug)
    name = member.name
    # Nullify task assignments rather than cascade-deleting tasks
    session.query(Task).filter(Task.assignee_id == member.id).update(
        {"assignee_id": None}, synchronize_session="fetch"
    )
    record_change(session, "staff", member.id, "delete", snapshot_instance(member))
    session.delete(member)
    session.flush()
    return name


def assign_to_property(session: Session, staff_id_or_slug: str, property_id_or_slug: str) -> Staff:
    member = resolve_identifier(session, Staff, staff_id_or_slug)
    prop = resolve_identifier(session, Property, property_id_or_slug)
    if prop not in member.properties:
        old_props = [p.name for p in member.properties]
        member.properties.append(prop)
        session.flush()
        new_props = [p.name for p in member.properties]
        record_change(session, "staff", member.id, "update", {"properties": {"old": old_props, "new": new_props}})
    return member


def remove_from_property(session: Session, staff_id_or_slug: str, property_id_or_slug: str) -> Staff:
    member = resolve_identifier(session, Staff, staff_id_or_slug)
    prop = resolve_identifier(session, Property, property_id_or_slug)
    if prop in member.properties:
        old_props = [p.name for p in member.properties]
        member.properties.remove(prop)
        session.flush()
        new_props = [p.name for p in member.properties]
        record_change(session, "staff", member.id, "update", {"properties": {"old": old_props, "new": new_props}})
    return member


def list_by_property(session: Session, property_id_or_slug: str) -> list[Staff]:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    return [s for s in prop.staff_members if s.active]
