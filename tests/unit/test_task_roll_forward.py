"""Repeating tasks reset in place: rolling the due date forward (2026-10-16).

User decision: a late task keeps its rhythm — it skips the dates it missed and comes back on the
next one after today, never already overdue.
"""

from __future__ import annotations

from datetime import date

import pytest

from mihomes.services.task import roll_forward

TODAY = date(2026, 10, 16)  # a Friday


@pytest.mark.parametrize(
    "freq, due, expected",
    [
        ("daily", date(2026, 10, 16), date(2026, 10, 17)),   # on time
        ("daily", date(2026, 10, 10), date(2026, 10, 17)),   # 6 days late → tomorrow, not Oct 11
        ("daily", date(2026, 10, 20), date(2026, 10, 21)),   # finished early → the one after
        ("weekly", date(2026, 10, 12), date(2026, 10, 19)),  # Monday, done Friday → next Monday
        ("weekly", date(2026, 9, 28), date(2026, 10, 19)),   # three Mondays late → still a Monday
        ("biweekly", date(2026, 10, 2), date(2026, 10, 30)),
        ("monthly", date(2026, 9, 5), date(2026, 11, 5)),    # missed Oct 5 → Nov 5
        ("quarterly", date(2026, 7, 1), date(2027, 1, 1)),
        ("annual", date(2025, 3, 1), date(2027, 3, 1)),
    ],
)
def test_keeps_the_rhythm_and_lands_after_today(freq, due, expected):
    assert roll_forward(freq, due, TODAY) == expected


def test_no_due_date_starts_from_today():
    assert roll_forward("daily", None, TODAY) == date(2026, 10, 17)
    assert roll_forward("weekly", None, TODAY) == date(2026, 10, 23)


def test_result_is_always_after_today():
    for freq in ("daily", "weekly", "biweekly", "monthly", "quarterly", "annual"):
        assert roll_forward(freq, date(2024, 1, 31), TODAY) > TODAY


def test_once_has_no_next_date():
    assert roll_forward("once", date(2026, 10, 10), TODAY) is None


def test_seasonal_skips_to_the_next_season_after_today():
    # default calendar: spring Mar 1, fall Sep 1
    assert roll_forward("seasonal", date(2026, 3, 1), TODAY, season_spec="spring,fall") == date(2027, 3, 1)


def test_seasonal_without_seasons_ends():
    assert roll_forward("seasonal", date(2026, 3, 1), TODAY, season_spec=None) is None


class TestDoneForNow:
    """A finished repeating task shows under Done until its next due date (user, 2026-10-16)."""

    def _task(self, due, *, every="weekly", status="pending", rolled_to="same", done=True):
        from types import SimpleNamespace

        from mihomes.models.task import RecurrenceFrequency, TaskStatus
        completions = []
        if done:
            completions = [SimpleNamespace(rolled_to=due if rolled_to == "same" else rolled_to)]
        return SimpleNamespace(
            status=TaskStatus(status), due_date=due, completions=completions,
            schedule=SimpleNamespace(frequency=RecurrenceFrequency(every)) if every else None,
        )

    def test_done_weekly_stays_done_until_next_week(self):
        from mihomes.services.task import is_done_for_now
        t = self._task(date(2026, 10, 23))  # done today, next due in a week
        assert is_done_for_now(t, TODAY)
        assert is_done_for_now(t, date(2026, 10, 22))  # the day before it's due

    def test_returns_to_to_do_on_its_due_date(self):
        from mihomes.services.task import is_done_for_now
        t = self._task(date(2026, 10, 23))
        assert not is_done_for_now(t, date(2026, 10, 23))  # due day → To Do
        assert not is_done_for_now(t, date(2026, 10, 30))  # and stays there if not done

    def test_daily_is_done_today_and_back_tomorrow(self):
        from mihomes.services.task import is_done_for_now
        t = self._task(date(2026, 10, 17), every="daily")
        assert is_done_for_now(t, TODAY)
        assert not is_done_for_now(t, date(2026, 10, 17))

    def test_never_completed_is_not_done(self):
        from mihomes.services.task import is_done_for_now
        assert not is_done_for_now(self._task(date(2026, 10, 23), done=False), TODAY)

    def test_due_date_moved_by_hand_means_due_again(self):
        """The last completion rolled it to Oct 23; someone moved it to Oct 20 → it's due, not done."""
        from mihomes.services.task import is_done_for_now
        t = self._task(date(2026, 10, 20), rolled_to=date(2026, 10, 23))
        assert not is_done_for_now(t, TODAY)

    def test_one_time_and_in_progress_are_never_done_for_now(self):
        from mihomes.services.task import is_done_for_now
        assert not is_done_for_now(self._task(date(2026, 10, 23), every=None), TODAY)
        assert not is_done_for_now(self._task(date(2026, 10, 23), every="once"), TODAY)
        assert not is_done_for_now(self._task(date(2026, 10, 23), status="in-progress"), TODAY)


def test_month_end_clamping_is_stable():
    # Jan 31 → Feb 28 → Mar 28 …: once clamped, the day stays clamped (calculate_next_due's rule)
    assert roll_forward("monthly", date(2026, 1, 31), date(2026, 3, 1)) == date(2026, 3, 28)
