"""Tasks by day / week / month — the agenda view (2026-10-15).

Pure functions over already-loaded tasks, so the period maths and the recurring-preview rules are
unit-testable without a database.

**Recurring previews.** A recurring task is one row: the next occurrence is created only when the
current one is completed (`task._advance_recurring_task`). So a daily task would otherwise show on
one day of a week view and look missing on the other six. The agenda therefore also lists the
dates its repeats *will* fall on, as previews. They are computed by chaining
`recurrence.calculate_next_due` from the real due date — the same function, same inputs, same
chaining that completion uses — so a preview never sits on a date the real repeat won't land on
(monthly clamping makes Jan 31 → Feb 28 → Mar 28, not Mar 31). Nothing is saved for a preview.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta

from mihomes.models.task import TaskStatus
from mihomes.services.recurrence import calculate_next_due

SPANS = ("day", "week", "month")

#: Upper bound on previews per task in one period. A daily task over a month is ~30; this only
#: stops a pathological schedule from looping, it never trims a real month.
_MAX_PREVIEWS = 62

_OPEN = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)


@dataclass
class AgendaEntry:
    task: object
    on: date
    preview: bool = False  # True = a future repeat of a recurring task, not a saved task


@dataclass
class AgendaDay:
    on: date
    entries: list[AgendaEntry] = field(default_factory=list)


@dataclass
class Agenda:
    span: str
    start: date
    end: date
    anchor: date
    today: date
    days: list[AgendaDay]
    overdue: list[object]
    unscheduled: list[object]

    @property
    def label(self) -> str:
        # `d.day` rather than `%-d`: the no-padding flag is glibc-only and raises on Windows.
        s, e = self.start, self.end
        if self.span == "day":
            return f"{s.strftime('%A, %B')} {s.day}, {s.year}"
        if self.span == "month":
            return s.strftime("%B %Y")
        if s.month == e.month:
            return f"{s.strftime('%b')} {s.day}–{e.day}, {e.year}"
        if s.year == e.year:
            return f"{s.strftime('%b')} {s.day} – {e.strftime('%b')} {e.day}, {e.year}"
        return f"{s.strftime('%b')} {s.day}, {s.year} – {e.strftime('%b')} {e.day}, {e.year}"

    @property
    def prev_anchor(self) -> date:
        return shift(self.span, self.anchor, -1)

    @property
    def next_anchor(self) -> date:
        return shift(self.span, self.anchor, 1)

    @property
    def count(self) -> int:
        return sum(len(d.entries) for d in self.days)


def normalise_span(span: str | None) -> str:
    return span if span in SPANS else "week"


def period_bounds(span: str, anchor: date) -> tuple[date, date]:
    """First and last day (inclusive) of the period containing `anchor`. Weeks run Mon–Sun."""
    if span == "day":
        return anchor, anchor
    if span == "month":
        return anchor.replace(day=1), anchor.replace(day=monthrange(anchor.year, anchor.month)[1])
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=6)


def shift(span: str, anchor: date, step: int) -> date:
    """The anchor one period earlier (`step=-1`) or later (`step=1`)."""
    if span == "day":
        return anchor + timedelta(days=step)
    if span == "week":
        return anchor + timedelta(weeks=step)
    month = anchor.month - 1 + step
    year = anchor.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _is_open(task) -> bool:
    return task.status in _OPEN


def _recurring(task) -> bool:
    schedule = getattr(task, "schedule", None)
    return bool(schedule) and schedule.frequency.value != "once"


def preview_dates(task, start: date, end: date) -> list[date]:
    """Dates in [start, end] that an open recurring task's future repeats will fall on.

    Chains `calculate_next_due` from the real due date, exactly as completion does, so each
    preview is the date the corresponding real occurrence would get.
    """
    if not (_is_open(task) and _recurring(task) and task.due_date):
        return []
    schedule = task.schedule
    prop = getattr(task, "property", None)
    climate_zone = getattr(prop, "climate_zone", None)
    dates: list[date] = []
    current = task.due_date
    for _ in range(_MAX_PREVIEWS * 8):  # walk forward past the period start, then collect
        nxt = calculate_next_due(
            schedule.frequency.value, current,
            climate_zone=climate_zone, season_spec=schedule.season_spec,
        )
        if nxt is None or nxt <= current or nxt > end:
            break
        if nxt >= start:
            dates.append(nxt)
            if len(dates) >= _MAX_PREVIEWS:
                break
        current = nxt
    return dates


def build_agenda(tasks, span: str, anchor: date, today: date) -> Agenda:
    """Bucket `tasks` into the period containing `anchor`.

    - Overdue: open tasks due before **today** (not before the period), so they never vanish by
      paging to a later week — the thing a reader most needs to see stays at the top.
    - Each day of the period: tasks due that day (any status, so completed work shows where it
      fell), plus previews of open recurring tasks' future repeats.
    - Unscheduled: open tasks with no due date.
    """
    span = normalise_span(span)
    start, end = period_bounds(span, anchor)
    days = {start + timedelta(days=i): AgendaDay(start + timedelta(days=i))
            for i in range((end - start).days + 1)}

    overdue, unscheduled = [], []
    for task in tasks:
        if task.status == TaskStatus.CANCELLED:
            continue
        due = task.due_date
        if due is None:
            if _is_open(task):
                unscheduled.append(task)
            continue
        if _is_open(task) and due < today:
            overdue.append(task)
        elif start <= due <= end:
            days[due].entries.append(AgendaEntry(task, due))
        for on in preview_dates(task, start, end):
            days[on].entries.append(AgendaEntry(task, on, preview=True))

    _rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    for day in days.values():
        day.entries.sort(key=lambda e: (e.preview, _rank.get(getattr(e.task.priority, "value", ""), 9),
                                        e.task.title.lower()))
    overdue.sort(key=lambda t: (t.due_date, t.title.lower()))
    unscheduled.sort(key=lambda t: t.title.lower())

    return Agenda(span=span, start=start, end=end, anchor=anchor, today=today,
                  days=list(days.values()), overdue=overdue, unscheduled=unscheduled)
