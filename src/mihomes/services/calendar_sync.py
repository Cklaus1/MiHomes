"""Calendar sync service — bidirectional sync between MiHomes and Google Calendar."""

import os
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from sqlalchemy.orm import Session


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


def push_upcoming_to_google(session: Session, days: int = 30,
                             property_id_or_slug: str | None = None) -> dict:
    """Push all upcoming tasks with due dates to Google Calendar.

    Deduplicates by gcal_event_id stored on the task — never re-pushes a task
    that has already been successfully pushed.
    """
    if not is_google_auth_available():
        return {"pushed": 0, "errors": [], "skipped": "not authenticated"}

    from mihomes.services.task import get_upcoming_tasks
    from mihomes.models.task import Task
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

    from mihomes.services.property import list_properties, occupy_property
    from mihomes.models.task import Task
    props = list_properties(session)

    pulled, tasks_created, errors = 0, 0, []

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
        matched_prop = None
        title_lower = title.lower()
        desc_lower = desc.lower()
        for prop in props:
            if prop.name.lower() in title_lower or prop.name.lower() in desc_lower:
                matched_prop = prop
                break
        # Default to primary/estate property if no match
        if not matched_prop and props:
            for prop in props:
                if prop.property_type and prop.property_type.value in ("estate", "primary"):
                    matched_prop = prop
                    break
            if not matched_prop:
                matched_prop = props[0]

        if not matched_prop:
            continue

        # Vendor/task events → create a task instead of occupancy
        if _is_task_event(title, desc):
            try:
                # Avoid duplicates: skip if a task with same title + property + due_date exists
                existing = session.query(Task).filter(
                    Task.title == title,
                    Task.property_id == matched_prop.id,
                    Task.due_date == start_date,
                ).first()
                if not existing:
                    from mihomes.services.task import create_task
                    create_task(session, title, str(matched_prop.id),
                                description=desc or None, due_date=start_date)
                    tasks_created += 1
            except Exception as e:
                errors.append(f"{title} (task): {e}")
        else:
            try:
                occupy_property(session, str(matched_prop.id), start_date, end_date)
                pulled += 1
            except Exception as e:
                errors.append(f"{title}: {e}")

    return {"pulled": pulled, "tasks_created": tasks_created, "errors": errors}


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
