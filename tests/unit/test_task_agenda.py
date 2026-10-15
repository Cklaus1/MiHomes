"""Tasks agenda view — period maths and recurring previews (2026-10-15)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from mihomes.models.task import RecurrenceFrequency, TaskPriority, TaskStatus
from mihomes.services.recurrence import calculate_next_due
from mihomes.services.task_agenda import (
    build_agenda,
    period_bounds,
    preview_dates,
    shift,
)

TODAY = date(2026, 10, 15)  # a Thursday


def _task(title, due=None, *, status=TaskStatus.PENDING, priority=TaskPriority.MEDIUM,
          every=None, season=None):
    schedule = (SimpleNamespace(frequency=RecurrenceFrequency(every), season_spec=season)
                if every else None)
    return SimpleNamespace(title=title, due_date=due, status=status, priority=priority,
                           schedule=schedule, property=SimpleNamespace(climate_zone=None))


class TestPeriods:
    def test_week_runs_monday_to_sunday(self):
        assert period_bounds("week", TODAY) == (date(2026, 10, 12), date(2026, 10, 18))

    def test_week_containing_a_sunday(self):
        assert period_bounds("week", date(2026, 10, 18)) == (date(2026, 10, 12), date(2026, 10, 18))

    def test_month(self):
        assert period_bounds("month", TODAY) == (date(2026, 10, 1), date(2026, 10, 31))

    def test_leap_february(self):
        assert period_bounds("month", date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))

    def test_day(self):
        assert period_bounds("day", TODAY) == (TODAY, TODAY)

    @pytest.mark.parametrize(
        "span, anchor, step, expected",
        [
            ("day", date(2026, 12, 31), 1, date(2027, 1, 1)),
            ("week", TODAY, -1, date(2026, 10, 8)),
            ("month", date(2026, 1, 31), 1, date(2026, 2, 28)),  # clamps, no ValueError
            ("month", date(2026, 12, 15), 1, date(2027, 1, 15)),
            ("month", date(2026, 1, 15), -1, date(2025, 12, 15)),
        ],
    )
    def test_shift(self, span, anchor, step, expected):
        assert shift(span, anchor, step) == expected


class TestBuckets:
    def test_overdue_unscheduled_and_days(self):
        late = _task("Late", date(2026, 10, 1))
        mon = _task("Monday job", date(2026, 10, 12))
        sun = _task("Sunday job", date(2026, 10, 18))
        floating = _task("Someday")
        next_week = _task("Next week", date(2026, 10, 20))
        agenda = build_agenda([late, mon, sun, floating, next_week], "week", TODAY, TODAY)

        assert agenda.overdue == [late]
        assert agenda.unscheduled == [floating]
        by_day = {d.on: [e.task.title for e in d.entries] for d in agenda.days}
        assert by_day[date(2026, 10, 12)] == ["Monday job"]
        assert by_day[date(2026, 10, 18)] == ["Sunday job"]
        assert "Next week" not in sum(by_day.values(), [])
        assert len(agenda.days) == 7

    def test_overdue_stays_visible_when_paging_ahead(self):
        late = _task("Late", date(2026, 10, 1))
        agenda = build_agenda([late], "week", date(2026, 11, 20), TODAY)
        assert agenda.overdue == [late]

    def test_completed_shows_on_its_day_but_is_never_overdue(self):
        done = _task("Done", date(2026, 10, 13), status=TaskStatus.COMPLETED)
        agenda = build_agenda([done], "week", TODAY, TODAY)
        assert agenda.overdue == []
        assert [e.task for d in agenda.days for e in d.entries] == [done]

    def test_cancelled_never_appears(self):
        gone = _task("Gone", date(2026, 10, 13), status=TaskStatus.CANCELLED)
        agenda = build_agenda([gone, _task("Gone2", status=TaskStatus.CANCELLED)], "week", TODAY, TODAY)
        assert agenda.count == 0 and not agenda.overdue and not agenda.unscheduled

    def test_urgent_sorts_first_and_previews_last(self):
        low = _task("a low", date(2026, 10, 14), priority=TaskPriority.LOW)
        urgent = _task("z urgent", date(2026, 10, 14), priority=TaskPriority.URGENT)
        daily = _task("daily", date(2026, 10, 13), every="daily")
        agenda = build_agenda([low, urgent, daily], "week", TODAY, TODAY)
        tue = next(d for d in agenda.days if d.on == date(2026, 10, 14))
        assert [(e.task.title, e.preview) for e in tue.entries] == [
            ("z urgent", False), ("a low", False), ("daily", True),
        ]


class TestPreviews:
    def test_daily_fills_the_rest_of_the_week(self):
        daily = _task("Feed fish", date(2026, 10, 13), every="daily")
        agenda = build_agenda([daily], "week", TODAY, TODAY)
        real = [d.on for d in agenda.days for e in d.entries if not e.preview]
        previews = [d.on for d in agenda.days for e in d.entries if e.preview]
        assert real == [date(2026, 10, 13)]
        assert previews == [date(2026, 10, d) for d in range(14, 19)]

    def test_weekly_over_a_month(self):
        weekly = _task("Mow", date(2026, 10, 2), every="weekly")
        dates = preview_dates(weekly, date(2026, 10, 1), date(2026, 10, 31))
        assert dates == [date(2026, 10, 9), date(2026, 10, 16), date(2026, 10, 23), date(2026, 10, 30)]

    def test_monthly_clamping_matches_completion(self):
        """Jan 31 → Feb 28 → Mar 28: the preview must follow the same chain completion does."""
        monthly = _task("Rent check", date(2026, 1, 31), every="monthly")
        chained, current = [], monthly.due_date
        for _ in range(3):
            current = calculate_next_due("monthly", current)
            chained.append(current)
        assert chained == [date(2026, 2, 28), date(2026, 3, 28), date(2026, 4, 28)]
        assert preview_dates(monthly, date(2026, 3, 1), date(2026, 3, 31)) == [date(2026, 3, 28)]

    def test_overdue_recurring_still_previews_forward(self):
        daily = _task("Water", date(2026, 10, 10), every="daily")
        agenda = build_agenda([daily], "week", TODAY, TODAY)
        assert agenda.overdue == [daily]
        previews = [d.on for d in agenda.days for e in d.entries if e.preview]
        assert previews[0] == date(2026, 10, 12) and previews[-1] == date(2026, 10, 18)

    @pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.CANCELLED])
    def test_closed_tasks_never_preview(self, status):
        t = _task("x", date(2026, 10, 13), status=status, every="daily")
        assert preview_dates(t, date(2026, 10, 12), date(2026, 10, 18)) == []

    def test_one_off_and_undated_never_preview(self):
        assert preview_dates(_task("once", date(2026, 10, 13), every="once"),
                             date(2026, 10, 1), date(2026, 10, 31)) == []
        assert preview_dates(_task("undated", every="daily"),
                             date(2026, 10, 1), date(2026, 10, 31)) == []

    def test_daily_over_a_month_is_bounded_and_complete(self):
        daily = _task("x", date(2026, 9, 30), every="daily")
        assert len(preview_dates(daily, date(2026, 10, 1), date(2026, 10, 31))) == 31


def test_labels():
    assert build_agenda([], "week", TODAY, TODAY).label == "Oct 12–18, 2026"
    assert build_agenda([], "month", TODAY, TODAY).label == "October 2026"
    assert build_agenda([], "day", TODAY, TODAY).label == "Thursday, October 15, 2026"
    assert build_agenda([], "week", date(2026, 12, 30), TODAY).label == "Dec 28, 2026 – Jan 3, 2027"
