"""Tests for appointment service — mark serviced -> budget transaction."""

from datetime import date

import pytest

from mihomes.models.budget import Transaction
from mihomes.models.property import Property, PropertyType
from mihomes.models.recurring_expense import ExpenseFrequency
from mihomes.services.appointment import create_appointment, mark_appointment_serviced
from mihomes.services.recurring import create_recurring_expense


@pytest.fixture
def prop(session):
    p = Property(name="Appointment House", slug="appointment-house", property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def pest_control_expense(session, prop):
    return create_recurring_expense(
        session, "Pest Control", 132.25, ExpenseFrequency.CUSTOM_MONTHS,
        str(prop.id), "pest_control", date.today(), interval_count=2,
    )


class TestMarkAppointmentServiced:
    def test_creates_budget_transaction(self, session, prop, pest_control_expense):
        appt = create_appointment(
            session, "Pest Control", str(prop.id), date.today(),
            recurring_expense_id=pest_control_expense.id,
        )
        mark_appointment_serviced(session, appt.id)
        session.expire(appt)
        assert appt.completed is True
        tx = session.query(Transaction).filter(Transaction.appointment_id == appt.id).first()
        assert tx is not None
        assert tx.amount == 132.25
        assert tx.source == "recurring_expense"

    def test_uses_actual_cost_override(self, session, prop, pest_control_expense):
        appt = create_appointment(
            session, "Pest Control", str(prop.id), date.today(),
            recurring_expense_id=pest_control_expense.id,
        )
        mark_appointment_serviced(session, appt.id, actual_cost=150.0)
        tx = session.query(Transaction).filter(Transaction.appointment_id == appt.id).first()
        assert tx.amount == 150.0

    def test_second_call_raises(self, session, prop, pest_control_expense):
        appt = create_appointment(
            session, "Pest Control", str(prop.id), date.today(),
            recurring_expense_id=pest_control_expense.id,
        )
        mark_appointment_serviced(session, appt.id)
        with pytest.raises(ValueError):
            mark_appointment_serviced(session, appt.id)
        # Only one transaction ever gets created, no matter how many attempts
        count = session.query(Transaction).filter(Transaction.appointment_id == appt.id).count()
        assert count == 1

    def test_raises_if_not_linked_to_recurring_expense(self, session, prop):
        appt = create_appointment(session, "One-off Visit", str(prop.id), date.today())
        with pytest.raises(ValueError):
            mark_appointment_serviced(session, appt.id)
