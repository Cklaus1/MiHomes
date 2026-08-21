"""Staff service — CRUD and property assignment."""

import uuid

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.staff import Staff, StaffRole, category_for_role
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.update_helpers import safe_update
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
    user_id: uuid.UUID | None = None,
) -> Staff:
    """Create a staff record.

    `user_id` links the record to a MiHomes login (SPEC-003 U6) and is what
    `authz/query_scope.py` filters `PERSONNEL` on, so it decides who may read this row. It is
    keyword-only and deliberately absent from the web form's parameter list — see the note on
    `update_staff`.
    """
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
        user_id=user_id,
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
    """Update a staff record from arbitrary keyword arguments.

    **`user_id` is settable here, and that is a sharper edge than it looks.** `safe_update` applies
    any key matching a real column, so `update_staff(db, slug, user_id=...)` works — and after
    SPEC-003 U6 that column decides *who may read the row*, since `authz/query_scope.py` filters
    `PERSONNEL` on it. A staff member who could set it on their own record could point it at a
    colleague's and read that record instead.

    They cannot, today, and the reason is worth stating rather than relying on: `web/routes/staff.py
    edit_staff` builds its `kwargs` from an explicit list of named `Form(...)` parameters, so an
    extra field in the POST body is discarded by FastAPI before this function sees it — and
    `tests/unit/test_staff_user_link.py::test_the_edit_form_cannot_set_user_id` pins that. If a
    future route ever forwards raw form data here, the filter must move into this function.
    """
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
    from mihomes.models.issue import Issue
    from mihomes.models.staff_pto import StaffPTORequest
    from mihomes.models.task import Task
    from mihomes.models.work_order import WorkOrder

    member = resolve_identifier(session, Staff, id_or_slug)
    name = member.name
    # Clear every reference to this person before deleting, or FK enforcement
    # (PRAGMA foreign_keys=ON) would block the delete. Nullify the optional
    # references; PTO requests have a NOT NULL staff_id so they're removed.
    session.query(Task).filter(Task.assignee_id == member.id).update(
        {"assignee_id": None}, synchronize_session="fetch"
    )
    session.query(WorkOrder).filter(WorkOrder.assignee_id == member.id).update(
        {"assignee_id": None}, synchronize_session="fetch"
    )
    session.query(Issue).filter(Issue.reported_by_id == member.id).update(
        {"reported_by_id": None}, synchronize_session="fetch"
    )
    session.query(Issue).filter(Issue.resolved_by_id == member.id).update(
        {"resolved_by_id": None}, synchronize_session="fetch"
    )
    session.query(StaffPTORequest).filter(StaffPTORequest.staff_id == member.id).delete(
        synchronize_session="fetch"
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
