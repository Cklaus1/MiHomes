"""Reconcile one calendar connection: what the person should see vs what was last written.

**One idempotent function, and every trigger calls it** — connecting, "Sync now", the save
trigger and the `calendar-sync` job. Tasks change through a dozen paths (complete, roll forward,
reset, archive, clear, import, AI tools, Telegram); hooking each would miss one. Instead the
sync derives the desired events from the database and diffs them against
`calendar_event_links` by fingerprint, so whatever changed a task, the next sync notices.

Rules, in the order they matter:

- **Never call Google inside a database transaction.** Read and close, call Google, then write
  each link in its own short transaction. A transaction held across HTTP is how a slow Google
  becomes a stuck page (the session-touch deadlock, 2026-09).
- **Refuse to run without the account bound.** Under RLS an unbound read sees *nothing*, and a
  sync that sees nothing deletes every event. `require_account()` at entry, and the
  membership must be readable — that read is the proof the tenant context is real.
- **Event ids are ours**, derived from (connection, item), so a lost response retried cannot
  make a twin (`google_client.put_event`).
- **Only delete for a reason**: the item is gone, finished, or no longer visible to this person
  (staff scope narrowed, membership revoked). An item that merely aged out of the window keeps
  its event — history stays in the calendar.
- **Concurrency without a lock across HTTP**: a lease on the connection row; a sync requested
  while one runs sets `needs_sync_at`, and the running one goes round again.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select, update

from mihomes.models.appointment import Appointment
from mihomes.models.calendar_connection import CalendarConnection, CalendarEventLink
from mihomes.models.membership import Membership
from mihomes.models.property import Property
from mihomes.models.task import Task, TaskStatus
from mihomes.services.personal_calendar.google_client import (
    CalendarGone,
    GoogleBusy,
    GoogleError,
    ReconnectNeeded,
)
from mihomes.tenancy.context import require_account

logger = logging.getLogger(__name__)

WINDOW_PAST = timedelta(days=30)
WINDOW_FUTURE = timedelta(days=365)
#: How long a run may hold the connection before another may take over (a crashed process).
LEASE = timedelta(minutes=10)
#: Passes per run when more changes arrive while syncing.
MAX_PASSES = 3

_OPEN_TASK = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)


@dataclass
class SyncResult:
    status: str = "ok"  # ok | busy | skipped | reconnect | error
    written: int = 0
    deleted: int = 0
    unchanged: int = 0
    error: str | None = None


@dataclass
class _Plan:
    refresh_token: str
    calendar_id: str | None
    calendar_name: str
    time_zone: str
    desired: dict[tuple[str, uuid.UUID], tuple[dict, str]] = field(default_factory=dict)
    links: dict[tuple[str, uuid.UUID], tuple[str, str]] = field(default_factory=dict)
    to_delete: list[tuple[str, uuid.UUID]] = field(default_factory=list)


def event_id_for(connection_id, entity_type: str, entity_id) -> str:
    """Google requires base32hex (`a-v`, `0-9`), 5–1024 chars; hex digits are a subset."""
    return hashlib.sha256(f"{connection_id}:{entity_type}:{entity_id}".encode()).hexdigest()


def _base_url() -> str:
    return os.environ.get("MIHOMES_BASE_URL", "https://app.mihomes.ai").rstrip("/")


def _fingerprint(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _all_day(d: date) -> dict:
    # Google's all-day `end.date` is EXCLUSIVE: a one-day event ends the next day.
    return {"start": {"date": d.isoformat()},
            "end": {"date": (d + timedelta(days=1)).isoformat()}}


def appointment_body(appt: Appointment, prop: Property | None, time_zone: str) -> dict:
    where = prop.name if prop else None
    vendor = getattr(getattr(appt, "vendor", None), "company_name", None)
    summary = appt.title + (f" — {vendor}" if vendor else "")
    if appt.start_time:
        start = datetime.combine(appt.date, appt.start_time)
        end = start + timedelta(hours=1)
        when = {"start": {"dateTime": start.isoformat(), "timeZone": time_zone},
                "end": {"dateTime": end.isoformat(), "timeZone": time_zone}}
    else:
        when = _all_day(appt.date)
    lines = [appt.notes or "", f"Property: {where}" if where else "",
             f"Open in MiHomes: {_base_url()}/calendar"]
    body = {"summary": summary, "description": "\n".join(x for x in lines if x), **when}
    if prop is not None and (prop.address or prop.name):
        body["location"] = prop.address or prop.name
    return body


def task_body(task: Task, prop: Property | None) -> dict:
    lines = [task.description or "", f"Property: {prop.name}" if prop else "",
             f"Open in MiHomes: {_base_url()}/tasks"]
    body = {"summary": f"Task: {task.title}",
            "description": "\n".join(x for x in lines if x), **_all_day(task.due_date)}
    if prop is not None and (prop.address or prop.name):
        body["location"] = prop.address or prop.name
    return body


def _desired(session, visible: frozenset, today: date, time_zone: str) -> dict:
    out: dict[tuple[str, uuid.UUID], tuple[dict, str]] = {}
    if not visible:
        return out
    start, end = today - WINDOW_PAST, today + WINDOW_FUTURE
    props = {p.id: p for p in session.execute(
        select(Property).where(Property.id.in_(visible))).scalars()}

    for appt in session.execute(select(Appointment).where(
            Appointment.property_id.in_(visible),
            Appointment.date >= start, Appointment.date <= end)).scalars():
        body = appointment_body(appt, props.get(appt.property_id), time_zone)
        out[("appointment", appt.id)] = (body, _fingerprint(body))

    for task in session.execute(select(Task).where(
            Task.property_id.in_(visible), Task.due_date.is_not(None),
            Task.due_date >= start, Task.due_date <= end,
            Task.status.in_(_OPEN_TASK), Task.archived_at.is_(None))).scalars():
        body = task_body(task, props.get(task.property_id))
        out[("task", task.id)] = (body, _fingerprint(body))
    return out


def _aged_out(session, visible: frozenset, keys, today: date) -> set:
    """Of these linked items, the ones still valid but simply older than the window."""
    start = today - WINDOW_PAST
    keep: set = set()
    appt_ids = [i for t, i in keys if t == "appointment"]
    task_ids = [i for t, i in keys if t == "task"]
    if appt_ids and visible:
        keep |= {("appointment", i) for i in session.execute(select(Appointment.id).where(
            Appointment.id.in_(appt_ids), Appointment.property_id.in_(visible),
            Appointment.date < start)).scalars()}
    if task_ids and visible:
        keep |= {("task", i) for i in session.execute(select(Task.id).where(
            Task.id.in_(task_ids), Task.property_id.in_(visible), Task.due_date < start,
            Task.status.in_(_OPEN_TASK), Task.archived_at.is_(None))).scalars()}
    return keep


def _plan(session_factory, connection_id) -> _Plan | None:
    from mihomes.authz.scope import scoped_property_ids
    from mihomes.crypto import decrypt
    from mihomes.models.account import Account
    from mihomes.services.config_service import estate_today, estate_zone

    with session_factory() as s:
        conn = s.get(CalendarConnection, connection_id)
        if conn is None or conn.status != "active":
            return None
        membership = s.get(Membership, conn.membership_id)
        if membership is None:
            # Under RLS this is what an unbound or wrong account context looks like. Stop:
            # carrying on would compute "nothing visible" and delete every event.
            raise RuntimeError("calendar sync: membership not readable; refusing to run")
        visible = scoped_property_ids(s, membership)
        today = estate_today(s)
        tz = estate_zone(s).key
        account = s.get(Account, conn.account_id)
        plan = _Plan(
            refresh_token=decrypt(conn.refresh_token),
            calendar_id=conn.calendar_id,
            calendar_name=f"MiHomes — {account.name if account else 'Estate'}",
            time_zone=tz,
            desired=_desired(s, visible, today, tz),
        )
        for link in s.execute(select(CalendarEventLink).where(
                CalendarEventLink.connection_id == connection_id)).scalars():
            plan.links[(link.entity_type, link.entity_id)] = (link.google_event_id,
                                                              link.fingerprint)
        stale = [k for k in plan.links if k not in plan.desired]
        keep = _aged_out(s, visible, stale, today)
        plan.to_delete = [k for k in stale if k not in keep]
        return plan


def _save_link(session_factory, connection_id, key, event_id: str, fingerprint: str) -> None:
    with session_factory() as s:
        link = s.execute(select(CalendarEventLink).where(
            CalendarEventLink.connection_id == connection_id,
            CalendarEventLink.entity_type == key[0],
            CalendarEventLink.entity_id == key[1])).scalar_one_or_none()
        if link is None:
            s.add(CalendarEventLink(connection_id=connection_id, entity_type=key[0],
                                    entity_id=key[1], google_event_id=event_id,
                                    fingerprint=fingerprint))
        else:
            link.google_event_id, link.fingerprint = event_id, fingerprint
        s.commit()


def _drop_link(session_factory, connection_id, key) -> None:
    with session_factory() as s:
        for link in s.execute(select(CalendarEventLink).where(
                CalendarEventLink.connection_id == connection_id,
                CalendarEventLink.entity_type == key[0],
                CalendarEventLink.entity_id == key[1])).scalars():
            s.delete(link)
        s.commit()


def _reset_calendar(session_factory, connection_id, calendar_id: str | None) -> None:
    """The calendar was deleted in Google (or is being created): forget every link."""
    with session_factory() as s:
        conn = s.get(CalendarConnection, connection_id)
        conn.calendar_id = calendar_id
        for link in s.execute(select(CalendarEventLink).where(
                CalendarEventLink.connection_id == connection_id)).scalars():
            s.delete(link)
        s.commit()


def _one_pass(session_factory, connection_id, client_factory, result: SyncResult,
              *, recreated: bool = False) -> bool:
    """Returns False when the connection is no longer syncable (gone / inactive)."""
    plan = _plan(session_factory, connection_id)
    if plan is None:
        return False
    client = client_factory(plan.refresh_token)

    if plan.calendar_id is None:
        plan.calendar_id = client.create_calendar(plan.calendar_name, plan.time_zone)
        _reset_calendar(session_factory, connection_id, plan.calendar_id)
        plan.links.clear()

    try:
        for key, (body, fp) in plan.desired.items():
            existing = plan.links.get(key)
            if existing is not None and existing[1] == fp:
                result.unchanged += 1
                continue
            event_id = event_id_for(connection_id, *key)
            client.put_event(plan.calendar_id, event_id, body)
            _save_link(session_factory, connection_id, key, event_id, fp)
            result.written += 1
        for key in plan.to_delete:
            client.delete_event(plan.calendar_id, plan.links[key][0])
            _drop_link(session_factory, connection_id, key)
            result.deleted += 1
    except CalendarGone:
        # Deleted in Google. Recreate it once rather than leave the person with a connection
        # that silently writes nowhere; a calendar gone again straight away is an error.
        _reset_calendar(session_factory, connection_id, None)
        if recreated:
            raise GoogleError("The MiHomes calendar keeps disappearing in Google") from None
        return _one_pass(session_factory, connection_id, client_factory, result, recreated=True)
    return True


def sync_connection(session_factory, connection_id, *, client_factory=None,
                    now: datetime | None = None) -> SyncResult:
    """Bring one connection's calendar in line with the estate. Never raises."""
    require_account()
    if client_factory is None:
        from mihomes.services.personal_calendar.google_client import GoogleCalendarClient

        client_factory = GoogleCalendarClient
    now = now or datetime.now(UTC)
    result = SyncResult()

    with session_factory() as s:
        claimed = s.execute(
            update(CalendarConnection)
            .where(CalendarConnection.id == connection_id,
                   CalendarConnection.status == "active",
                   or_(CalendarConnection.syncing_until.is_(None),
                       CalendarConnection.syncing_until < now))
            .values(syncing_until=now + LEASE, needs_sync_at=None)
            .returning(CalendarConnection.id)
        ).first()
        if claimed is None:
            conn = s.get(CalendarConnection, connection_id)
            if conn is None or conn.status != "active":
                result.status = "skipped"
            else:
                conn.needs_sync_at = now  # the running sync will go round again
                result.status = "busy"
            s.commit()
            return result
        s.commit()

    status, error = "active", None
    try:
        for _ in range(MAX_PASSES):
            started = datetime.now(UTC)
            if not _one_pass(session_factory, connection_id, client_factory, result):
                result.status = "skipped"
                break
            with session_factory() as s:
                conn = s.get(CalendarConnection, connection_id)
                again = conn is not None and conn.needs_sync_at and conn.needs_sync_at > started
                if again:
                    conn.needs_sync_at = None
                s.commit()
            if not again:
                break
    except ReconnectNeeded as exc:
        status, error, result.status = "needs_reconnect", str(exc), "reconnect"
    except GoogleBusy as exc:
        error, result.status = f"Google was busy; will retry ({exc})", "error"
    except GoogleError as exc:
        error, result.status = str(exc), "error"
    except Exception as exc:  # noqa: BLE001 — recorded on the connection, never raised to a save
        logger.exception("calendar sync failed for connection %s", connection_id)
        error, result.status = f"Sync failed: {type(exc).__name__}", "error"
    finally:
        with session_factory() as s:
            conn = s.get(CalendarConnection, connection_id)
            if conn is not None:
                conn.syncing_until = None
                conn.last_error = error
                if result.status == "ok":
                    conn.last_synced_at = datetime.now(UTC)
                if status == "needs_reconnect":
                    conn.status = status
            s.commit()
    result.error = error
    return result


def sync_account(session_factory, *, client_factory=None) -> list[SyncResult]:
    """Every active connection in the bound account."""
    require_account()
    with session_factory() as s:
        ids = list(s.execute(select(CalendarConnection.id).where(
            CalendarConnection.status == "active")).scalars())
    return [sync_connection(session_factory, i, client_factory=client_factory) for i in ids]
