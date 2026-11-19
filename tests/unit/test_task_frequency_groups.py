"""Tasks page Frequency view — grouping by how often a task repeats (2026-10-15)."""

from __future__ import annotations

from types import SimpleNamespace

from mihomes.models.task import RecurrenceFrequency
from mihomes.web.routes.tasks import FREQUENCY_ORDER, group_by_frequency


def _task(title, every=None):
    schedule = SimpleNamespace(frequency=RecurrenceFrequency(every)) if every else None
    return SimpleNamespace(title=title, schedule=schedule)


def test_tasks_land_in_their_frequency():
    daily, weekly, monthly = _task("d", "daily"), _task("w", "weekly"), _task("m", "monthly")
    groups = group_by_frequency([daily, weekly, monthly])
    assert groups["daily"] == [daily]
    assert groups["weekly"] == [weekly]
    assert groups["monthly"] == [monthly]


def test_no_schedule_and_once_are_one_time():
    bare, once = _task("bare"), _task("once", "once")
    assert group_by_frequency([bare, once])["once"] == [bare, once]


def test_every_frequency_has_a_section():
    """A frequency the enum allows but the view has no section for would vanish from the page."""
    keys = {key for key, _ in FREQUENCY_ORDER}
    assert {f.value for f in RecurrenceFrequency} <= keys


def test_order_preserved_within_a_group():
    a, b = _task("a", "weekly"), _task("b", "weekly")
    assert group_by_frequency([a, b])["weekly"] == [a, b]


class TestFormFrequency:
    """`_frequency` parses the create/edit forms' Repeats field."""

    def test_known_values(self):
        from mihomes.web.routes.tasks import _frequency
        assert _frequency("daily") is RecurrenceFrequency.DAILY
        assert _frequency(" Weekly ") is RecurrenceFrequency.WEEKLY

    def test_blank_or_unknown_is_once_not_an_error(self):
        from mihomes.web.routes.tasks import _frequency
        for value in (None, "", "fortnightly", "<script>"):
            assert _frequency(value) is RecurrenceFrequency.ONCE

    def test_form_offers_every_frequency_one_time_first(self):
        from mihomes.web.routes.tasks import FREQUENCY_ORDER
        options = [FREQUENCY_ORDER[-1]] + FREQUENCY_ORDER[:-1]
        assert options[0][0] == "once"
        assert {v for v, _ in options} == {f.value for f in RecurrenceFrequency}


def test_most_frequent_first_one_time_last():
    keys = [key for key, _ in FREQUENCY_ORDER]
    assert keys[0] == "daily" and keys[-1] == "once"
