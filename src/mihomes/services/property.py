"""Property service — CRUD operations with audit logging."""

from datetime import date

from sqlalchemy.orm import Session

from mihomes.models.property import Property, PropertyStatus, PropertyType
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.validators import validate_name


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
    name = validate_name(name, "property")
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

    safe_update(prop, kwargs)

    session.flush()
    new_snap = snapshot_instance(prop)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "property", prop.id, "update", changes)
    return prop


def delete_property(session: Session, id_or_slug: str) -> str:
    from mihomes.models.task import Task
    from mihomes.models.issue import Issue
    from mihomes.models.budget import Budget, Transaction
    from mihomes.models.asset import Asset

    prop = resolve_identifier(session, Property, id_or_slug)
    name = prop.name

    # Check for dependent records and delete them (cascade)
    session.query(Transaction).filter(Transaction.property_id == prop.id).delete()
    session.query(Budget).filter(Budget.property_id == prop.id).delete()
    session.query(Asset).filter(Asset.property_id == prop.id).delete()

    # Delete tasks and their schedules
    from mihomes.models.task import TaskSchedule
    task_ids = [t.id for t in session.query(Task.id).filter(Task.property_id == prop.id).all()]
    if task_ids:
        session.query(TaskSchedule).filter(TaskSchedule.task_id.in_(task_ids)).delete(synchronize_session="fetch")
        session.query(Task).filter(Task.property_id == prop.id).delete()

    session.query(Issue).filter(Issue.property_id == prop.id).delete()

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
    prop = update_property(
        session,
        id_or_slug,
        occupied=True,
        occupied_since=from_date or date.today(),
        occupied_until=until_date,
    )
    # Auto-generate guest turnover tasks on occupancy
    _run_occupancy_template(session, prop, "guest-turnover")
    return prop


def vacate_property(session: Session, id_or_slug: str) -> Property:
    prop = update_property(
        session,
        id_or_slug,
        occupied=False,
        occupied_since=None,
        occupied_until=None,
    )
    # Auto-generate post-departure turnover tasks on vacate
    _run_occupancy_template(session, prop, "guest-turnover")
    return prop


def _run_occupancy_template(session: Session, prop: Property, template_slug: str) -> None:
    """Silently run a template for a property if the template exists."""
    from mihomes.models.template import Template
    from mihomes.services.template import run_template
    tmpl = session.query(Template).filter(Template.slug == template_slug).first()
    if tmpl:
        run_template(session, template_slug, str(prop.id))
