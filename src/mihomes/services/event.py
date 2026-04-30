"""Event service — event and guest management."""

from datetime import date

from sqlalchemy.orm import Session

from mihomes.models.event import Event, EventGuest, EventStatus, Guest
from mihomes.models.property import Property
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.validators import validate_name


# --- Event CRUD ---

def create_event(
    session: Session,
    title: str,
    property_id_or_slug: str,
    event_date: date,
    *,
    end_date: date | None = None,
    expected_guests: int | None = None,
    budget: float | None = None,
    currency: str = "USD",
    description: str | None = None,
    notes: str | None = None,
    slug: str | None = None,
) -> Event:
    title = validate_name(title, "event")
    prop = resolve_identifier(session, Property, property_id_or_slug)
    slug = ensure_unique_slug(session, Event, slug or generate_slug(title))
    event = Event(
        title=title, slug=slug, property_id=prop.id,
        event_date=event_date, end_date=end_date,
        expected_guests=expected_guests, budget=budget, currency=currency,
        description=description, notes=notes,
    )
    session.add(event)
    session.flush()
    record_change(session, "event", event.id, "create", snapshot_instance(event))
    return event


def list_events(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    status: EventStatus | None = None,
) -> list[Event]:
    query = session.query(Event)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Event.property_id == prop.id)
    if status:
        query = query.filter(Event.status == status)
    return query.order_by(Event.event_date.desc()).all()


def get_event(session: Session, id_or_slug: str) -> Event:
    return resolve_identifier(session, Event, id_or_slug)


def update_event(session: Session, id_or_slug: str, **kwargs) -> Event:
    event = resolve_identifier(session, Event, id_or_slug)
    old_snap = snapshot_instance(event)
    if "title" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Event, generate_slug(kwargs["title"]), exclude_id=event.id)
    safe_update(event, kwargs)
    session.flush()
    new_snap = snapshot_instance(event)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "event", event.id, "update", changes)
    return event


def delete_event(session: Session, id_or_slug: str) -> str:
    event = resolve_identifier(session, Event, id_or_slug)
    name = event.title
    record_change(session, "event", event.id, "delete", snapshot_instance(event))
    session.delete(event)
    session.flush()
    return name


# --- Guest CRUD ---

def create_guest(
    session: Session,
    name: str,
    *,
    email: str | None = None,
    phone: str | None = None,
    dietary_preferences: str | None = None,
    room_preference: str | None = None,
    notes: str | None = None,
    slug: str | None = None,
) -> Guest:
    name = validate_name(name, "guest")
    slug = ensure_unique_slug(session, Guest, slug or generate_slug(name))
    guest = Guest(
        name=name, slug=slug, email=email, phone=phone,
        dietary_preferences=dietary_preferences,
        room_preference=room_preference, notes=notes,
    )
    session.add(guest)
    session.flush()
    record_change(session, "guest", guest.id, "create", snapshot_instance(guest))
    return guest


def update_guest(session: Session, id_or_slug: str, **kwargs) -> Guest:
    guest = resolve_identifier(session, Guest, id_or_slug)
    old_snap = snapshot_instance(guest)
    if "name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Guest, generate_slug(kwargs["name"]), exclude_id=guest.id)
    safe_update(guest, kwargs)
    session.flush()
    changes = diff_instance(old_snap, snapshot_instance(guest))
    if changes:
        record_change(session, "guest", guest.id, "update", changes)
    return guest


def list_guests(session: Session) -> list[Guest]:
    return session.query(Guest).order_by(Guest.name).all()


def get_guest(session: Session, id_or_slug: str) -> Guest:
    return resolve_identifier(session, Guest, id_or_slug)


def delete_guest(session: Session, id_or_slug: str) -> str:
    guest = resolve_identifier(session, Guest, id_or_slug)
    name = guest.name
    record_change(session, "guest", guest.id, "delete", snapshot_instance(guest))
    session.delete(guest)
    session.flush()
    return name


# --- EventGuest (invitations/RSVP) ---

def invite_guest(
    session: Session,
    event_id_or_slug: str,
    guest_id_or_slug: str,
    *,
    notes: str | None = None,
) -> EventGuest:
    event = resolve_identifier(session, Event, event_id_or_slug)
    guest = resolve_identifier(session, Guest, guest_id_or_slug)
    # Check if already invited
    existing = session.query(EventGuest).filter(
        EventGuest.event_id == event.id,
        EventGuest.guest_id == guest.id,
    ).first()
    if existing:
        raise ValueError(f"Guest '{guest.name}' is already invited to event '{event.title}'")
    eg = EventGuest(event_id=event.id, guest_id=guest.id, rsvp_status="invited", notes=notes)
    session.add(eg)
    session.flush()
    record_change(session, "event_guest", eg.id, "create", snapshot_instance(eg))
    return eg


VALID_RSVP_STATUSES = {"invited", "accepted", "declined", "maybe", "attended"}


def update_rsvp(
    session: Session,
    event_id_or_slug: str,
    guest_id_or_slug: str,
    rsvp_status: str,
) -> EventGuest:
    if rsvp_status not in VALID_RSVP_STATUSES:
        raise ValueError(f"Invalid RSVP status: '{rsvp_status}'. Valid: {sorted(VALID_RSVP_STATUSES)}")
    event = resolve_identifier(session, Event, event_id_or_slug)
    guest = resolve_identifier(session, Guest, guest_id_or_slug)
    eg = session.query(EventGuest).filter(
        EventGuest.event_id == event.id,
        EventGuest.guest_id == guest.id,
    ).first()
    if not eg:
        raise ValueError(f"Guest '{guest.name}' is not invited to event '{event.title}'")
    old_snap = snapshot_instance(eg)
    eg.rsvp_status = rsvp_status
    session.flush()
    new_snap = snapshot_instance(eg)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "event_guest", eg.id, "update", changes)
    return eg
