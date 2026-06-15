"""Appointment service — vendor visits, inspections, and scheduled events."""

from datetime import date, time

from sqlalchemy.orm import Session

from mihomes.models.appointment import Appointment, AppointmentType
from mihomes.models.property import Property
from mihomes.services.audit import record_change, snapshot_instance
from mihomes.services.slug import resolve_identifier
from mihomes.services.update_helpers import safe_update


def create_appointment(
    session: Session,
    title: str,
    property_id_or_slug: str,
    appt_date: date,
    *,
    vendor_id: int | None = None,
    contract_id: int | None = None,
    start_time: time | None = None,
    appointment_type: str = AppointmentType.VENDOR_VISIT,
    notes: str | None = None,
) -> Appointment:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    appt = Appointment(
        title=title,
        property_id=prop.id,
        vendor_id=vendor_id,
        contract_id=contract_id,
        date=appt_date,
        start_time=start_time,
        appointment_type=appointment_type,
        notes=notes,
    )
    session.add(appt)
    session.flush()
    record_change(session, "appointment", appt.id, "create", snapshot_instance(appt))

    from mihomes.services.calendar_sync import push_appointment_to_google
    push_appointment_to_google(appt, session)

    return appt


def list_appointments(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[Appointment]:
    query = session.query(Appointment)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Appointment.property_id == prop.id)
    if date_from:
        query = query.filter(Appointment.date >= date_from)
    if date_to:
        query = query.filter(Appointment.date <= date_to)
    return query.order_by(Appointment.date.asc(), Appointment.start_time.asc()).all()


def get_appointment(session: Session, appointment_id: int) -> Appointment:
    appt = session.get(Appointment, appointment_id)
    if appt is None:
        raise ValueError(f"Appointment {appointment_id} not found")
    return appt


def update_appointment(session: Session, appointment_id: int, **kwargs) -> Appointment:
    appt = get_appointment(session, appointment_id)
    old_snap = snapshot_instance(appt)
    safe_update(appt, kwargs)
    session.flush()
    from mihomes.services.audit import diff_instance
    changes = diff_instance(old_snap, snapshot_instance(appt))
    if changes:
        record_change(session, "appointment", appt.id, "update", changes)
    return appt


def delete_appointment(session: Session, appointment_id: int) -> None:
    appt = get_appointment(session, appointment_id)
    record_change(session, "appointment", appt.id, "delete", snapshot_instance(appt))
    session.delete(appt)
    session.flush()
