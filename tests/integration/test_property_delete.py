"""Deleting a property (UI/UX fix-all, 2026-10-21).

Thirteen tables reference `properties` with no ON DELETE rule and `delete_property` cleared only
four of them, so deleting any home with history — a completed task, a staff link, a book —
failed with a foreign-key error and a 500. Now: the property's own task history, zones and
staff/vendor links go with it; records with value of their own (books, contracts, work
orders, …) block the delete with a sentence saying what is attached.
"""

from __future__ import annotations

import pytest

from mihomes.models.book import Book
from mihomes.models.property import Property
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc
from mihomes.services import task as task_svc


def test_property_with_task_history_and_staff_link_deletes(session):
    prop = prop_svc.create_property(session, "Short Lived Cottage")
    task = task_svc.create_task(session, "Sweep", str(prop.id))
    task_svc.complete_task(session, str(task.id))
    staff = staff_svc.create_staff(session, "Pat Helper")
    staff.properties.append(prop)
    session.flush()

    assert prop_svc.delete_property(session, prop.slug) == "Short Lived Cottage"
    session.flush()
    assert session.get(Property, prop.id) is None


def test_property_with_books_refuses_with_a_reason(session):
    prop = prop_svc.create_property(session, "Library House")
    session.add(Book(title="Moby-Dick", slug="moby-dick", property_id=prop.id))
    session.add(Book(title="Emma", slug="emma", property_id=prop.id))
    session.flush()

    with pytest.raises(ValueError, match="still has 2 books"):
        prop_svc.delete_property(session, prop.slug)
    assert session.get(Property, prop.id) is not None
