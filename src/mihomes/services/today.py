"""The dashboard's Today list: everything dated today or already late, in one ranked list.

Answers "what needs me today?" without opening four cards. Only dated things belong here:
today's appointments and events, tasks due today, and overdue tasks. Issues are not dated, so
they stay in the dashboard's Open Issues card (worst first) rather than appearing twice.

Ranked with the SPACE order the AI advisor uses (Safety, Presence, Asset protection,
Compliance, Economy), mapped onto fields the data already has:

- **Safety:** tasks in a safety category (fire safety, security, health and environment).
- **Presence:** appointments and events today; someone has to be there.
- **Asset protection:** urgent and high-priority tasks.
- **Economy:** every other task.

Compliance has no dated source yet (contract and insurance expiries live in Alerts), so no row
gets it today. Within a group the most overdue comes first, then time of day. The groups only
order the list; the row's tag shows the task's own priority, which people already understand.

**Tasks are the viewer's own** (2026-11-04): those assigned to their staff record, plus
unassigned tasks for an owner or admin, who is responsible for anything nobody holds. A task
assigned to someone else (a gardener with no login, the housekeeper) belongs on that person's
list, not here. Appointments and events have no assignee and are shown to everyone who can see
the property. Staff see only their properties: every query here goes through the session's
property-scope hook.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from mihomes.models.appointment import Appointment
from mihomes.models.event import Event, EventStatus
from mihomes.models.task import Task, TaskCategory, TaskPriority
from mihomes.services import task as task_svc
from mihomes.services.localtime import today as local_today

_SAFETY_CATEGORIES = frozenset({
    TaskCategory.FIRE_SAFETY, TaskCategory.SECURITY, TaskCategory.HEALTH_ENV,
})

#: (rank, label) per SPACE group; the rank orders the list.
SAFETY = (0, "Safety")
PRESENCE = (1, "Presence")
ASSET = (2, "Asset")
ECONOMY = (4, "Economy")


@dataclass(frozen=True)
class TodayItem:
    kind: str            # "task" | "appointment" | "event"
    title: str
    property_name: str | None
    link: str
    space: str           # SPACE group: orders the list, not shown
    when: str            # "3 days overdue", "Today 10:30", "Today", "Day 2 of 3"
    late: bool
    _sort: tuple
    priority: str | None = None   # task priority ("urgent" … "low"); None for calendar items


def _task_space(task: Task) -> tuple[int, str]:
    if task.category in _SAFETY_CATEGORIES:
        return SAFETY
    if task.priority in (TaskPriority.URGENT, TaskPriority.HIGH):
        return ASSET
    return ECONOMY


_PRIORITY_RANK = {TaskPriority.URGENT: 0, TaskPriority.HIGH: 1, TaskPriority.MEDIUM: 2,
                  TaskPriority.LOW: 3}


def _task_item(task: Task, today: date) -> TodayItem:
    rank, label = _task_space(task)
    days_late = (today - task.due_date).days
    when = "Due today" if days_late <= 0 else (
        "1 day overdue" if days_late == 1 else f"{days_late} days overdue")
    return TodayItem(
        kind="task", title=task.title,
        property_name=task.property.name if task.property else None,
        link=f"/tasks/?open={task.id}", space=label, when=when, late=days_late > 0,
        _sort=(rank, -days_late, _PRIORITY_RANK.get(task.priority, 9), time.max, task.title),
        priority=task.priority.value if task.priority else None,
    )


def _calendar_link(day: date) -> str:
    return f"/calendar/?month={day:%Y-%m}"


def today_items(
    session: Session,
    today: date | None = None,
    *,
    user_id: uuid.UUID | None,
    include_unassigned: bool,
) -> list[TodayItem]:
    """`user_id` is the viewer; "their" tasks are `task_svc.viewer_task_filter`'s definition."""
    today = today or local_today()
    items: list[TodayItem] = []

    is_mine = task_svc.viewer_task_filter(session, user_id, include_unassigned=include_unassigned)
    tasks = task_svc.get_overdue_tasks(session) + task_svc.get_upcoming_tasks(session, days=0)
    items += [_task_item(t, today) for t in tasks if t.archived_at is None and is_mine(t)]

    rank, label = PRESENCE
    for appt in session.query(Appointment).filter(
        Appointment.date == today, Appointment.completed.is_(False),
    ):
        at = appt.start_time
        items.append(TodayItem(
            kind="appointment", title=appt.title,
            property_name=appt.property.name if appt.property else None,
            link=_calendar_link(today), space=label,
            when=f"Today {at:%H:%M}" if at else "Today", late=False,
            _sort=(rank, 0, 9, at or time.max, appt.title),
        ))

    for event in session.query(Event).filter(
        Event.event_date <= today,
        func.coalesce(Event.end_date, Event.event_date) >= today,
        or_(Event.status.is_(None),
            Event.status.notin_([EventStatus.CANCELLED, EventStatus.COMPLETED])),
    ):
        end = event.end_date or event.event_date
        span = (end - event.event_date).days + 1
        when = "Today" if span == 1 else f"Day {(today - event.event_date).days + 1} of {span}"
        items.append(TodayItem(
            kind="event", title=event.title,
            property_name=event.property.name if event.property else None,
            link=_calendar_link(today), space=label, when=when, late=False,
            _sort=(rank, 0, 9, time.min, event.title),
        ))

    return sorted(items, key=lambda i: i._sort)
