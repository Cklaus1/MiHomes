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


def is_google_auth_available() -> bool:
    return TOKEN_FILE.exists()


def _get_provider():
    from mihomes.services.gateways.calendar.google import GoogleCalendarProvider
    return GoogleCalendarProvider()


def push_task_to_google(task) -> bool:
    """Push a single task to Google Calendar. Returns True if successful."""
    if not is_google_auth_available():
        return False
    if not task.due_date:
        return False
    try:
        provider = _get_provider()
        start = datetime(task.due_date.year, task.due_date.month, task.due_date.day,
                         9, 0, tzinfo=timezone.utc)
        end = datetime(task.due_date.year, task.due_date.month, task.due_date.day,
                       10, 0, tzinfo=timezone.utc)
        provider.create_event(
            title=f"[MiHomes] {task.title}",
            start=start,
            end=end,
            description=task.description or "",
        )
        return True
    except Exception:
        return False


def push_upcoming_to_google(session: Session, days: int = 30,
                             property_id_or_slug: str | None = None) -> dict:
    """Push all upcoming tasks with due dates to Google Calendar."""
    if not is_google_auth_available():
        return {"pushed": 0, "errors": [], "skipped": "not authenticated"}

    from mihomes.services.task import get_upcoming_tasks
    tasks = get_upcoming_tasks(session, days=days,
                               property_id_or_slug=property_id_or_slug)

    pushed, errors = 0, []
    provider = _get_provider()

    for task in tasks:
        if not task.due_date:
            continue
        try:
            start = datetime(task.due_date.year, task.due_date.month,
                             task.due_date.day, 9, 0, tzinfo=timezone.utc)
            end = datetime(task.due_date.year, task.due_date.month,
                           task.due_date.day, 10, 0, tzinfo=timezone.utc)
            provider.create_event(
                title=f"[MiHomes] {task.title}",
                start=start,
                end=end,
                description=task.description or "",
            )
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
    props = list_properties(session)

    pulled, errors = 0, []

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

        # Try to match event to a property by name mention in title/description
        matched_prop = None
        desc = (event.get("description") or "").lower()
        title_lower = title.lower()
        for prop in props:
            if prop.name.lower() in title_lower or prop.name.lower() in desc:
                matched_prop = prop
                break
        # Default to first property (primary/estate) if no match
        if not matched_prop and props:
            for prop in props:
                if prop.property_type and prop.property_type.value in ("estate", "primary"):
                    matched_prop = prop
                    break
            if not matched_prop:
                matched_prop = props[0]

        if not matched_prop:
            continue

        try:
            occupy_property(session, str(matched_prop.id), start_date, end_date)
            pulled += 1
        except Exception as e:
            errors.append(f"{title}: {e}")

    return {"pulled": pulled, "errors": errors}


def auto_sync(session: Session) -> dict:
    """Run full bidirectional sync. Called by watchdog periodically."""
    push_result = push_upcoming_to_google(session, days=30)
    pull_result = pull_from_google(session, days=60)
    return {
        "pushed": push_result.get("pushed", 0),
        "pulled": pull_result.get("pulled", 0),
        "errors": push_result.get("errors", []) + pull_result.get("errors", []),
    }
