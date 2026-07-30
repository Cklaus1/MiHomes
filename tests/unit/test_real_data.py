"""L3 — real_data loader hardening.

Two defects:
  * ``load_real_data`` was not idempotent — a second call created a duplicate
    "Belle Estate" (with a slug suffix) plus every vendor/task again.
  * Recurring tasks were created with no ``due_date``, so their schedule's
    ``next_due`` was never computed (calculate_next_due only runs when a
    due_date seed is present) — the tasks never surfaced on the calendar.
"""

from datetime import date

import pytest

from mihomes.models.property import Property
from mihomes.models.task import Task, RecurrenceFrequency
from mihomes.services.real_data import load_real_data


def test_load_real_data_is_idempotent(session):
    load_real_data(session)
    session.commit()
    first_count = session.query(Property).count()

    # A second load must not duplicate properties (guard like load_demo_data).
    with pytest.raises(ValueError, match="already loaded"):
        load_real_data(session)

    session.rollback()
    assert session.query(Property).count() == first_count
    # exactly one Belle Estate, no slug-suffixed duplicate
    belles = session.query(Property).filter(Property.name == "Belle Estate").count()
    assert belles == 1


def test_recurring_tasks_have_due_dates(session):
    load_real_data(session)
    session.commit()

    recurring = (
        session.query(Task)
        .join(Task.schedule)
        .all()
    )
    assert recurring, "expected recurring tasks to be created"
    missing = [t.title for t in recurring if t.due_date is None]
    assert not missing, f"recurring tasks with no due_date: {missing[:5]}"
