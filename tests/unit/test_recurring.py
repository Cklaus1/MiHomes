"""Tests for recurring expense service — create, list, generate transactions."""

from datetime import date, timedelta

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.recurring_expense import ExpenseFrequency, RecurringExpense
from mihomes.models.budget import Transaction
from mihomes.services.recurring import (
    _next_due_date,
    create_recurring_expense,
    generate_transactions,
    list_recurring_expenses,
)


@pytest.fixture
def prop(session):
    p = Property(name="Recurring House", slug="recurring-house", property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


class TestNextDueDate:
    def _make_expense(self, freq, last_generated=None, start_date=None):
        from types import SimpleNamespace
        return SimpleNamespace(
            frequency=freq,
            last_generated=last_generated,
            start_date=start_date or date(2026, 1, 1),
        )

    def test_no_last_generated_returns_start_date(self):
        exp = self._make_expense(ExpenseFrequency.MONTHLY, start_date=date(2026, 3, 1))
        assert _next_due_date(exp) == date(2026, 3, 1)

    def test_weekly(self):
        exp = self._make_expense(ExpenseFrequency.WEEKLY, last_generated=date(2026, 1, 1))
        assert _next_due_date(exp) == date(2026, 1, 8)

    def test_biweekly(self):
        exp = self._make_expense(ExpenseFrequency.BIWEEKLY, last_generated=date(2026, 1, 1))
        assert _next_due_date(exp) == date(2026, 1, 15)

    def test_monthly(self):
        exp = self._make_expense(ExpenseFrequency.MONTHLY, last_generated=date(2026, 1, 15))
        assert _next_due_date(exp) == date(2026, 2, 15)

    def test_monthly_end_of_month_clamping(self):
        exp = self._make_expense(ExpenseFrequency.MONTHLY, last_generated=date(2026, 1, 31))
        assert _next_due_date(exp) == date(2026, 2, 28)

    def test_quarterly(self):
        exp = self._make_expense(ExpenseFrequency.QUARTERLY, last_generated=date(2026, 1, 1))
        assert _next_due_date(exp) == date(2026, 4, 1)

    def test_annual(self):
        exp = self._make_expense(ExpenseFrequency.ANNUAL, last_generated=date(2026, 3, 15))
        assert _next_due_date(exp) == date(2027, 3, 15)


class TestCreateRecurringExpense:
    def test_basic_create(self, session, prop):
        exp = create_recurring_expense(
            session, "Landscaping", 200.0, ExpenseFrequency.MONTHLY,
            str(prop.id), "landscaping", date(2026, 1, 1),
        )
        assert exp.id is not None
        assert exp.name == "Landscaping"
        assert exp.amount == 200.0
        assert exp.frequency == ExpenseFrequency.MONTHLY
        assert exp.active is True

    def test_create_with_all_fields(self, session, prop):
        exp = create_recurring_expense(
            session, "Pool Service", 150.0, ExpenseFrequency.WEEKLY,
            prop.slug, "maintenance", date(2026, 1, 1),
            currency="EUR", end_date=date(2026, 12, 31), notes="Weekly pool care",
        )
        assert exp.currency == "EUR"
        assert exp.end_date == date(2026, 12, 31)
        assert exp.notes == "Weekly pool care"

    def test_creates_audit_log(self, session, prop):
        from mihomes.models.audit_log import AuditLog
        count_before = session.query(AuditLog).count()
        create_recurring_expense(
            session, "Alarm System", 50.0, ExpenseFrequency.ANNUAL,
            str(prop.id), "security", date(2026, 1, 1),
        )
        assert session.query(AuditLog).count() > count_before

    def test_invalid_property_raises(self, session):
        from mihomes.services.slug import EntityNotFoundError
        with pytest.raises(EntityNotFoundError):
            create_recurring_expense(
                session, "Test", 100.0, ExpenseFrequency.MONTHLY,
                "nonexistent-property", "misc", date(2026, 1, 1),
            )


class TestListRecurringExpenses:
    def test_active_only_default(self, session, prop):
        create_recurring_expense(session, "Active Exp", 100.0, ExpenseFrequency.MONTHLY,
                                  str(prop.id), "misc", date(2026, 1, 1))
        # Add inactive
        inactive = RecurringExpense(
            name="Inactive Exp", amount=50.0, frequency=ExpenseFrequency.ANNUAL,
            property_id=prop.id, category="misc", start_date=date(2026, 1, 1), active=False,
        )
        session.add(inactive)
        session.flush()
        results = list_recurring_expenses(session)
        names = [r.name for r in results]
        assert "Active Exp" in names
        assert "Inactive Exp" not in names

    def test_include_inactive(self, session, prop):
        create_recurring_expense(session, "Active2", 100.0, ExpenseFrequency.MONTHLY,
                                  str(prop.id), "misc", date(2026, 1, 1))
        inactive = RecurringExpense(
            name="Inactive2", amount=50.0, frequency=ExpenseFrequency.ANNUAL,
            property_id=prop.id, category="misc", start_date=date(2026, 1, 1), active=False,
        )
        session.add(inactive)
        session.flush()
        results = list_recurring_expenses(session, active_only=False)
        names = [r.name for r in results]
        assert "Active2" in names
        assert "Inactive2" in names

    def test_sorted_by_name(self, session, prop):
        for name in ["Zebra", "Alpha", "Middle"]:
            create_recurring_expense(session, name, 100.0, ExpenseFrequency.MONTHLY,
                                      str(prop.id), "misc", date(2026, 1, 1))
        results = list_recurring_expenses(session)
        names = [r.name for r in results]
        assert names == sorted(names)


class TestGenerateTransactions:
    def test_generates_due_expense(self, session, prop):
        # Expense with start_date in the past and no last_generated → due
        create_recurring_expense(
            session, "Due Expense", 100.0, ExpenseFrequency.MONTHLY,
            str(prop.id), "utilities", date.today() - timedelta(days=1),
        )
        generated = generate_transactions(session)
        assert len(generated) >= 1
        tx = next((t for t in generated if t.description == "Recurring: Due Expense"), None)
        assert tx is not None
        assert tx.amount == 100.0
        assert tx.source == "recurring_expense"

    def test_skips_future_expense(self, session, prop):
        # Start date in the future → not due yet
        create_recurring_expense(
            session, "Future Expense", 200.0, ExpenseFrequency.MONTHLY,
            str(prop.id), "misc", date.today() + timedelta(days=10),
        )
        tx_count_before = session.query(Transaction).count()
        generate_transactions(session)
        # No new transaction for this future expense
        new_txs = session.query(Transaction).filter(
            Transaction.description == "Recurring: Future Expense"
        ).count()
        assert new_txs == 0

    def test_skips_ended_expense(self, session, prop):
        exp = create_recurring_expense(
            session, "Ended Expense", 50.0, ExpenseFrequency.MONTHLY,
            str(prop.id), "misc", date(2025, 1, 1),
            end_date=date(2025, 6, 1),
        )
        generated = generate_transactions(session)
        tx = next((t for t in generated if t.description == "Recurring: Ended Expense"), None)
        assert tx is None

    def test_updates_last_generated(self, session, prop):
        exp = create_recurring_expense(
            session, "Updatable Expense", 75.0, ExpenseFrequency.MONTHLY,
            str(prop.id), "misc", date.today() - timedelta(days=5),
        )
        assert exp.last_generated is None
        generate_transactions(session)
        session.expire(exp)
        assert exp.last_generated is not None

    def test_skips_inactive_expense(self, session, prop):
        inactive = RecurringExpense(
            name="Inactive Recurring", amount=100.0, frequency=ExpenseFrequency.MONTHLY,
            property_id=prop.id, category="misc",
            start_date=date.today() - timedelta(days=1), active=False,
        )
        session.add(inactive)
        session.flush()
        generate_transactions(session)
        tx = session.query(Transaction).filter(
            Transaction.description == "Recurring: Inactive Recurring"
        ).first()
        assert tx is None
