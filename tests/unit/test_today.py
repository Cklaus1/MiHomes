"""The dashboard's Today list: what is in it, and in what order (2026-11-03)."""

from datetime import date, datetime, time, timedelta

import pytest

from mihomes.models.appointment import Appointment
from mihomes.models.event import Event, EventStatus
from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskCategory, TaskPriority, TaskStatus
from mihomes.services.today import today_items

TODAY = date.today()


@pytest.fixture
def prop(session):
    p = Property(name="Today House", slug="today-house", property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


def _task(session, prop, title, *, due, priority=TaskPriority.MEDIUM, category=None, **kw):
    t = Task(title=title, slug=title.lower().replace(" ", "-"), property_id=prop.id,
             priority=priority, category=category, due_date=due, **kw)
    session.add(t)
    session.flush()
    return t


def _titles(session):
    return [i.title for i in today_items(session, TODAY)]


def test_ranked_safety_presence_asset_economy(session, prop):
    _task(session, prop, "Water plants", due=TODAY - timedelta(days=9), priority=TaskPriority.LOW)
    _task(session, prop, "Fix roof", due=TODAY, priority=TaskPriority.URGENT)
    session.add(Appointment(title="Plumber visit", property_id=prop.id, date=TODAY,
                            start_time=time(10, 30)))
    _task(session, prop, "Test smoke alarms", due=TODAY - timedelta(days=1),
          category=TaskCategory.FIRE_SAFETY)
    session.flush()

    items = today_items(session, TODAY)
    assert [i.title for i in items] == ["Test smoke alarms", "Plumber visit", "Fix roof", "Water plants"]
    assert [i.space for i in items] == ["Safety", "Presence", "Asset", "Economy"]
    assert items[1].when == "Today 10:30"
    assert items[0].when == "1 day overdue" and items[0].late
    assert items[2].when == "Due today" and not items[2].late


def test_most_overdue_first_within_a_group(session, prop):
    _task(session, prop, "Late a bit", due=TODAY - timedelta(days=2))
    _task(session, prop, "Late a lot", due=TODAY - timedelta(days=20))
    assert _titles(session) == ["Late a lot", "Late a bit"]


def test_links_open_the_record(session, prop):
    task = _task(session, prop, "Linked", due=TODAY)
    (item,) = today_items(session, TODAY)
    assert item.link == f"/tasks/?open={task.id}"
    assert item.property_name == "Today House"


def test_leaves_out_what_is_not_for_today(session, prop):
    _task(session, prop, "Tomorrow", due=TODAY + timedelta(days=1))
    _task(session, prop, "Done", due=TODAY - timedelta(days=3), status=TaskStatus.COMPLETED)
    _task(session, prop, "Archived", due=TODAY - timedelta(days=3),
          archived_at=datetime(2026, 1, 1))
    session.add(Appointment(title="Already serviced", property_id=prop.id, date=TODAY,
                            completed=True))
    session.add(Appointment(title="Next week", property_id=prop.id,
                            date=TODAY + timedelta(days=7)))
    session.add(Event(title="Cancelled party", slug="cancelled-party", property_id=prop.id,
                      event_date=TODAY, status=EventStatus.CANCELLED))
    session.flush()
    assert _titles(session) == []


def test_multi_day_event_shows_which_day(session, prop):
    session.add(Event(title="Family visit", slug="family-visit", property_id=prop.id,
                      event_date=TODAY - timedelta(days=1), end_date=TODAY + timedelta(days=1),
                      status=EventStatus.CONFIRMED))
    session.flush()
    (item,) = today_items(session, TODAY)
    assert (item.kind, item.space, item.when) == ("event", "Presence", "Day 2 of 3")
    assert item.link == f"/calendar/?month={TODAY:%Y-%m}"
