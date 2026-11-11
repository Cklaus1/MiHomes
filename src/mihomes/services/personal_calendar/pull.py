"""Google → MiHomes: read the "MiHomes — <estate>" calendar and apply what people changed there.

User decisions, 2026-11-11:

- **Who may change estate records from Google**: owners, admins, and staff an owner/admin
  granted (`memberships.can_import_calendar`). Everyone else's Google edits are overwritten by
  MiHomes on the next push, and their deletions are put back.
- An event **added** in Google becomes an appointment — on a property named in its location or
  title, else the person's default import property. Repeating Google series are skipped.
- **Edits** to appointment events flow back (title, date, start time; notes too for events that
  began in Google). **Deletes** delete the appointment, whichever side created it.
- **Tasks stay MiHomes-controlled**: an edited or deleted "Task:" event is put back.

Safety rules — each closes a way this could damage the whole estate's data:

- **Our own writes come back** through the incremental feed. They are recognised by Google's
  etag, stored on the link when we write — never by re-parsing content, which drifts.
- **The first pull is a baseline**: it records etags and the cursor and changes nothing on
  linked events, so turning this on cannot rewrite every appointment.
- **Conflicts**: if MiHomes changed the appointment since the last push (the link's fingerprint
  no longer matches), MiHomes wins and the Google edit is overwritten.
- **Deletes act only on linked events**, in properties the person can see. MiHomes' own
  deletions drop the link first, so they never come back as a delete. Serviced appointments and
  ones tied to an expense or contract are refused (the event is put back). More than
  `MAX_DELETES` in one pull applies none.
- **Crash-safe**: all pages are fetched first; each event is applied in its own transaction,
  appointment and link together; the cursor is saved last.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from mihomes.models.appointment import Appointment, AppointmentType
from mihomes.models.calendar_connection import CalendarConnection, CalendarEventLink
from mihomes.models.membership import Membership
from mihomes.models.property import Property
from mihomes.services.personal_calendar.google_client import SyncTokenExpired

logger = logging.getLogger(__name__)

MAX_DELETES = 10
_OUR_ID = re.compile(r"^[0-9a-f]{64}$")  # `sync.event_id_for` — never imported as new


@dataclass
class PullResult:
    imported: int = 0
    updated: int = 0
    deleted: int = 0
    put_back: int = 0
    skipped_recurring: int = 0
    skipped_no_property: int = 0
    refused_deletes: int = 0
    held_deletes: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class _Ctx:
    connection_id: object
    account_id: object
    user_id: object
    permitted: bool
    visible: frozenset
    default_property_id: object
    sync_token: str | None
    time_zone: str
    zone: object
    today: date


def may_import(membership: Membership) -> bool:
    return membership.status == "active" and (
        membership.role in ("owner", "admin") or bool(membership.can_import_calendar))


def _load(session_factory, connection_id) -> _Ctx | None:
    from mihomes.authz.scope import scoped_property_ids
    from mihomes.services.config_service import estate_today, estate_zone

    with session_factory() as s:
        conn = s.get(CalendarConnection, connection_id)
        if conn is None or conn.status != "active" or conn.calendar_id is None:
            return None
        m = s.get(Membership, conn.membership_id)
        if m is None or m.status != "active":
            return None
        zone = estate_zone(s)
        return _Ctx(connection_id=conn.id, account_id=conn.account_id, user_id=m.user_id,
                    permitted=may_import(m), visible=scoped_property_ids(s, m),
                    default_property_id=conn.default_property_id, sync_token=conn.sync_token,
                    time_zone=zone.key, zone=zone, today=estate_today(s))


def _when(ev: dict, zone) -> tuple[date | None, time | None]:
    start = ev.get("start") or {}
    if "date" in start:
        return date.fromisoformat(start["date"]), None
    if "dateTime" in start:
        dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(zone)
        return dt.date(), dt.time().replace(second=0, microsecond=0)
    return None, None


def _match_property(session, ev: dict, ctx: _Ctx) -> Property | None:
    props = [p for p in session.execute(
        select(Property).where(Property.id.in_(ctx.visible))).scalars()] if ctx.visible else []
    location = (ev.get("location") or "").strip().lower()
    if location:
        for p in props:
            if location in {(p.name or "").lower(), (p.address or "").strip().lower()}:
                return p
    title = (ev.get("summary") or "").lower()
    named = [p for p in props if p.name and p.name.lower() in title]
    if named:
        return max(named, key=lambda p: len(p.name))
    if ctx.default_property_id in ctx.visible:
        return next((p for p in props if p.id == ctx.default_property_id), None)
    return None


def _strip_vendor(summary: str, appt: Appointment) -> str:
    vendor = getattr(getattr(appt, "vendor", None), "company_name", None)
    suffix = f" — {vendor}" if vendor else None
    if suffix and summary.endswith(suffix):
        return summary[: -len(suffix)]
    return summary


def _deletable(session, appt: Appointment) -> bool:
    from mihomes.models.budget import Transaction

    if appt.completed or appt.recurring_expense_id or appt.contract_id:
        return False
    return not session.execute(select(func.count()).select_from(Transaction).where(
        Transaction.appointment_id == appt.id)).scalar()


def pull(session_factory, connection_id, client, *, calendar_id: str) -> PullResult:
    """Apply the Google side's changes for one connection. The caller holds the lease."""
    from mihomes.services.personal_calendar.sync import _fingerprint, appointment_body
    from mihomes.tenancy import account_context

    result = PullResult()
    ctx = _load(session_factory, connection_id)
    if ctx is None:
        return result

    try:
        events, cursor = client.list_changes(calendar_id, ctx.sync_token)
    except SyncTokenExpired:
        ctx.sync_token = None
        events, cursor = client.list_changes(calendar_id, None)
    baseline = ctx.sync_token is None

    with session_factory() as s:
        links = {link.google_event_id: (link.id, link.entity_type, link.entity_id, link.origin,
                                        link.etag, link.fingerprint)
                 for link in s.execute(select(CalendarEventLink).where(
                     CalendarEventLink.connection_id == connection_id)).scalars()}

    window = (ctx.today - timedelta(days=30), ctx.today + timedelta(days=365))
    deletes: list[tuple] = []

    # Writes are attributed to the person whose calendar it is.
    with account_context(ctx.account_id, user_id=ctx.user_id):
        for ev in events:
            gid = ev.get("id")
            link = links.get(gid)
            cancelled = ev.get("status") == "cancelled"
            if link is not None and ev.get("etag") and ev.get("etag") == link[4]:
                continue  # our own write coming back

            if link is None:
                if cancelled or not ctx.permitted or _OUR_ID.match(gid or ""):
                    continue
                _import(session_factory, ctx, ev, window, result)
                continue

            link_id, kind, entity_id, origin, _etag, fingerprint = link
            if baseline:
                if cancelled:
                    _drop(session_factory, link_id)  # push puts it back
                else:
                    _set(session_factory, link_id, etag=ev.get("etag"))
                continue

            if kind != "appointment" or not ctx.permitted:
                # Tasks, and anyone without permission: MiHomes stays in charge.
                if cancelled:
                    _drop(session_factory, link_id)
                else:
                    _set(session_factory, link_id, fingerprint="", etag=ev.get("etag"))
                result.put_back += 1
                continue

            if cancelled:
                deletes.append((link_id, entity_id))
                continue
            _apply_edit(session_factory, ctx, ev, link_id, entity_id, origin, fingerprint,
                        result, _fingerprint, appointment_body)

        if len(deletes) > MAX_DELETES:
            result.held_deletes = len(deletes)
            result.notes.append(
                f"{len(deletes)} appointments were deleted in Google at once — none were deleted "
                "in MiHomes. They'll reappear in Google; delete them in MiHomes if intended.")
            for link_id, _ in deletes:
                _drop(session_factory, link_id)
        else:
            for link_id, entity_id in deletes:
                _apply_delete(session_factory, ctx, link_id, entity_id, result)

    if result.skipped_recurring:
        result.notes.append(f"{result.skipped_recurring} repeating Google event(s) weren't "
                            "imported — add repeating appointments in MiHomes.")
    if result.skipped_no_property:
        result.notes.append(f"{result.skipped_no_property} event(s) weren't imported: no "
                            "property named and no default import property set.")
    if result.refused_deletes:
        result.notes.append(f"{result.refused_deletes} appointment(s) with costs or contracts "
                            "can't be deleted from Google; they were put back.")

    with session_factory() as s:
        conn = s.get(CalendarConnection, connection_id)
        if conn is not None:
            conn.sync_token = cursor or None
            conn.import_note = " ".join(result.notes) or None
        s.commit()
    return result


def _import(session_factory, ctx: _Ctx, ev: dict, window, result: PullResult) -> None:
    from mihomes.services.appointment import create_appointment
    from mihomes.services.personal_calendar.sync import _fingerprint, appointment_body

    if ev.get("recurrence") or ev.get("recurringEventId"):
        result.skipped_recurring += 1
        return
    day, at = _when(ev, ctx.zone)
    if day is None or not (window[0] <= day <= window[1]):
        return
    with session_factory() as s:
        prop = _match_property(s, ev, ctx)
        if prop is None:
            result.skipped_no_property += 1
            return
        title = (ev.get("summary") or "").strip() or "Untitled"
        appt = create_appointment(s, title[:300], str(prop.id), day, start_time=at,
                                  appointment_type=AppointmentType.OTHER,
                                  notes=(ev.get("description") or None))
        body = appointment_body(appt, prop, ctx.time_zone)
        s.add(CalendarEventLink(connection_id=ctx.connection_id, entity_type="appointment",
                                entity_id=appt.id, google_event_id=ev["id"], origin="google",
                                etag=ev.get("etag"), fingerprint=_fingerprint(body)))
        s.commit()
    result.imported += 1


def _apply_edit(session_factory, ctx: _Ctx, ev: dict, link_id, entity_id, origin, fingerprint,
                result: PullResult, _fingerprint, appointment_body) -> None:
    from mihomes.services.appointment import update_appointment

    with session_factory() as s:
        appt = s.get(Appointment, entity_id)
        link = s.get(CalendarEventLink, link_id)
        if appt is None or link is None or appt.property_id not in ctx.visible:
            if link is not None:
                s.delete(link)
            s.commit()
            return
        prop = s.get(Property, appt.property_id)
        if _fingerprint(appointment_body(appt, prop, ctx.time_zone)) != fingerprint:
            # MiHomes changed since the last push: MiHomes wins; the push overwrites Google.
            link.etag = ev.get("etag")
            s.commit()
            result.put_back += 1
            return
        day, at = _when(ev, ctx.zone)
        changes = {"title": _strip_vendor((ev.get("summary") or appt.title).strip(), appt)[:300]}
        if day is not None:
            changes["date"] = day
            changes["start_time"] = at
        if origin == "google":
            changes["notes"] = ev.get("description") or None
        if any(getattr(appt, k) != v for k, v in changes.items()):
            update_appointment(s, appt.id, **changes)
            result.updated += 1
        link.etag = ev.get("etag")
        link.fingerprint = _fingerprint(appointment_body(appt, prop, ctx.time_zone))
        s.commit()


def _apply_delete(session_factory, ctx: _Ctx, link_id, entity_id, result: PullResult) -> None:
    from mihomes.services.appointment import delete_appointment

    with session_factory() as s:
        link = s.get(CalendarEventLink, link_id)
        appt = s.get(Appointment, entity_id)
        if appt is None or appt.property_id not in ctx.visible:
            if link is not None:
                s.delete(link)
            s.commit()
            return
        if not _deletable(s, appt):
            if link is not None:
                s.delete(link)  # the push recreates the event
            s.commit()
            result.refused_deletes += 1
            return
        # This connection's link goes with it (its event is already deleted in Google). Other
        # members' links stay: their next push finds the appointment gone and deletes their copy.
        for other in s.execute(select(CalendarEventLink).where(
                CalendarEventLink.entity_type == "appointment",
                CalendarEventLink.entity_id == appt.id,
                CalendarEventLink.connection_id == ctx.connection_id)).scalars():
            s.delete(other)
        delete_appointment(s, appt.id)
        s.commit()
    result.deleted += 1


def _drop(session_factory, link_id) -> None:
    with session_factory() as s:
        link = s.get(CalendarEventLink, link_id)
        if link is not None:
            s.delete(link)
        s.commit()


def _set(session_factory, link_id, **values) -> None:
    with session_factory() as s:
        link = s.get(CalendarEventLink, link_id)
        if link is not None:
            for k, v in values.items():
                setattr(link, k, v)
        s.commit()
