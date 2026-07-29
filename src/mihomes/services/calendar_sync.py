"""Calendar sync service — bidirectional sync between MiHomes and Google Calendar."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mihomes.services.task import get_upcoming_tasks

TOKEN_FILE = Path(os.path.expanduser("~/.mihomes/google_token.json"))
VENDOR_CATEGORIES = {
    "vendor_activity", "maintenance", "repair", "service", "inspection",
    "hvac", "plumbing", "electrical", "pest-control", "landscaping",
    "pool", "irrigation", "elevator", "roofing", "cleaning",
}

# Keywords that indicate a Google Calendar event should become a MiHomes task
_TASK_KEYWORDS = {
    "irrigation", "maintenance", "repair", "service", "inspection",
    "hvac", "plumbing", "electrical", "pest", "landscaping", "lawn",
    "pool", "elevator", "roofing", "roof", "cleaning", "clean",
    "painting", "paint", "installation", "install", "replace", "check",
    "fix", "contractor", "vendor", "technician", "delivery", "trim",
    "pressure wash", "gutter", "filter", "tune-up", "tuneup",
}


def _is_task_event(title: str, description: str = "") -> bool:
    """Return True if this Google Calendar event looks like a vendor/task activity."""
    text = (title + " " + description).lower()
    return any(kw in text for kw in _TASK_KEYWORDS)


def is_google_auth_available() -> bool:
    return TOKEN_FILE.exists()


def _get_provider():
    from mihomes.services.gateways.calendar.google import GoogleCalendarProvider
    return GoogleCalendarProvider()


def push_task_to_google(task, session=None) -> bool:
    """Push a single task to Google Calendar. Skips if already pushed (gcal_event_id set)."""
    if not is_google_auth_available():
        return False
    if not task.due_date:
        return False
    if task.gcal_event_id:
        return True  # Already pushed — trust the stored ID, no API call needed
    try:
        provider = _get_provider()
        gcal_title = f"[MiHomes] {task.title}"
        start = datetime(task.due_date.year, task.due_date.month, task.due_date.day,
                         9, 0, tzinfo=timezone.utc)
        end = datetime(task.due_date.year, task.due_date.month, task.due_date.day,
                       10, 0, tzinfo=timezone.utc)
        result = provider.create_event(
            title=gcal_title,
            start=start,
            end=end,
            description=task.description or "",
        )
        # Store the event ID so we never push this task again
        if session and result.get("event_id"):
            task.gcal_event_id = result["event_id"]
            session.flush()
        return True
    except Exception:
        return False


def push_appointment_to_google(appt, session=None) -> bool:
    """Push a single appointment to Google Calendar. Skips if already pushed."""
    if not is_google_auth_available():
        return False
    if appt.gcal_event_id:
        return True
    try:
        provider = _get_provider()
        hour = appt.start_time.hour if appt.start_time else 9
        minute = appt.start_time.minute if appt.start_time else 0
        start = datetime(appt.date.year, appt.date.month, appt.date.day,
                         hour, minute, tzinfo=timezone.utc)
        end = datetime(appt.date.year, appt.date.month, appt.date.day,
                       hour + 1, minute, tzinfo=timezone.utc)
        vendor_note = f" ({appt.vendor.company_name})" if appt.vendor else ""
        result = provider.create_event(
            title=f"[MiHomes] {appt.title}{vendor_note}",
            start=start,
            end=end,
            description=appt.notes or "",
        )
        if session and result.get("event_id"):
            appt.gcal_event_id = result["event_id"]
            session.flush()
        return True
    except Exception:
        return False


def push_upcoming_to_google(session: Session, days: int = 30,
                             property_id_or_slug: str | None = None) -> dict:
    """Push all upcoming tasks with due dates to Google Calendar.

    Deduplicates by gcal_event_id stored on the task — never re-pushes a task
    that has already been successfully pushed.
    """
    if not is_google_auth_available():
        return {"pushed": 0, "errors": [], "skipped": "not authenticated"}

    tasks = get_upcoming_tasks(session, days=days,
                               property_id_or_slug=property_id_or_slug)
    # Only push tasks with a due date that haven't been pushed yet
    tasks = [t for t in tasks if t.due_date and not t.gcal_event_id]
    if not tasks:
        return {"pushed": 0, "errors": []}

    provider = _get_provider()
    pushed, errors = 0, []

    for task in tasks:
        gcal_title = f"[MiHomes] {task.title}"
        try:
            start = datetime(task.due_date.year, task.due_date.month,
                             task.due_date.day, 9, 0, tzinfo=timezone.utc)
            end = datetime(task.due_date.year, task.due_date.month,
                           task.due_date.day, 10, 0, tzinfo=timezone.utc)
            result = provider.create_event(
                title=gcal_title,
                start=start,
                end=end,
                description=task.description or "",
            )
            if result.get("event_id"):
                task.gcal_event_id = result["event_id"]
                session.flush()
            pushed += 1
        except Exception as e:
            errors.append(f"{task.title}: {e}")

    return {"pushed": pushed, "errors": errors}


def pull_from_google(session: Session, days: int = 60) -> dict:
    """Pull Google Calendar events into MiHomes occupancy and dashboard."""
    if not is_google_auth_available():
        return {"pulled": 0, "errors": [], "skipped": "not authenticated"}

    try:
        provider = _get_provider()
        now = datetime.now(timezone.utc)
        events = provider.list_events(now, now + timedelta(days=days))
    except Exception as e:
        return {"pulled": 0, "errors": [str(e)]}

    if not events:
        return {"pulled": 0, "errors": []}

    from mihomes.models.task import Task
    from mihomes.services.property import list_properties, occupy_property
    props = list_properties(session)

    pulled, tasks_created, errors = 0, 0, []
    skipped_unmatched = 0

    for event in events:
        title = event.get("title", "")
        # Skip events we pushed ourselves to avoid loops
        if title.startswith("[MiHomes]"):
            continue

        start = event.get("start")
        end = event.get("end")
        if not start:
            continue

        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else (end or start_date)
        desc = event.get("description") or ""

        # Try to match event to a property by name mention in title/description
        # H20: an event that names no property is genuinely ambiguous. Silently
        # attaching it to the primary/estate property fabricated occupancy and
        # tasks against the wrong home. Skip unmatched events entirely (both the
        # task and occupancy branches) and report the skip.
        matched_prop = None
        title_lower = title.lower()
        desc_lower = desc.lower()
        for prop in props:
            if prop.name.lower() in title_lower or prop.name.lower() in desc_lower:
                matched_prop = prop
                break

        if not matched_prop:
            skipped_unmatched += 1
            continue

        # Vendor/task events → create a task instead of occupancy
        if _is_task_event(title, desc):
            try:
                # H20: dedup on the Google event id first, then fall back to the
                # title+property+due_date natural key. The id match makes repeat
                # pulls idempotent even if the title changes upstream.
                existing = None
                gcal_id = event.get("id")
                if gcal_id:
                    existing = session.query(Task).filter(
                        Task.gcal_event_id == gcal_id,
                    ).first()
                if existing is None:
                    existing = session.query(Task).filter(
                        Task.title == title,
                        Task.property_id == matched_prop.id,
                        Task.due_date == start_date,
                    ).first()
                if existing:
                    # If re-discovered and still missing the event ID, backfill it
                    if not existing.gcal_event_id and gcal_id:
                        existing.gcal_event_id = gcal_id
                        session.flush()
                else:
                    from mihomes.services.task import create_task
                    new_task = create_task(session, title, str(matched_prop.id),
                                           description=desc or None, due_date=start_date)
                    # Store the Google Calendar event ID so push never re-pushes this task
                    if gcal_id:
                        new_task.gcal_event_id = gcal_id
                        session.flush()
                    tasks_created += 1
            except Exception as e:
                errors.append(f"{title} (task): {e}")
        else:
            try:
                # H20: only (re)occupy when the window actually changed. Re-running
                # a pull over an unchanged stay previously re-ran the turnover
                # template every time, duplicating tasks on each sync.
                if (matched_prop.occupied
                        and matched_prop.occupied_since == start_date
                        and matched_prop.occupied_until == end_date):
                    continue
                occupy_property(session, str(matched_prop.id), start_date, end_date)
                pulled += 1
            except Exception as e:
                errors.append(f"{title}: {e}")

    return {
        "pulled": pulled,
        "tasks_created": tasks_created,
        "skipped_unmatched": skipped_unmatched,
        "errors": errors,
    }


def auto_sync(session: Session) -> dict:
    """Run full bidirectional sync. Called by watchdog periodically."""
    push_result = push_upcoming_to_google(session, days=30)
    pull_result = pull_from_google(session, days=60)
    return {
        "pushed": push_result.get("pushed", 0),
        "pulled": pull_result.get("pulled", 0),
        "tasks_created": pull_result.get("tasks_created", 0),
        "errors": push_result.get("errors", []) + pull_result.get("errors", []),
    }
