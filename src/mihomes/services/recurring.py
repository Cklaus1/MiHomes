"""Recurring expense service — manage and generate transactions."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.models.budget import Transaction
from mihomes.models.property import Property
from mihomes.models.recurring_expense import ExpenseFrequency, RecurringExpense
from mihomes.models.vendor import Vendor
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.recurrence import add_months
from mihomes.services.slug import resolve_identifier
from mihomes.services.update_helpers import safe_update


def create_recurring_expense(
    session: Session,
    name: str,
    amount: float,
    frequency: ExpenseFrequency,
    property_id_or_slug: str,
    category: str,
    start_date: date,
    *,
    vendor_id_or_slug: str | None = None,
    currency: str = "USD",
    end_date: date | None = None,
    notes: str | None = None,
    interval_count: int | None = None,
) -> RecurringExpense:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    vendor_id = None
    if vendor_id_or_slug:
        vendor = resolve_identifier(session, Vendor, vendor_id_or_slug)
        vendor_id = vendor.id
    expense = RecurringExpense(
        name=name, amount=amount, currency=currency, frequency=frequency,
        property_id=prop.id, vendor_id=vendor_id, category=category,
        start_date=start_date, end_date=end_date, notes=notes,
        interval_count=interval_count,
    )
    session.add(expense)
    session.flush()
    record_change(session, "recurring_expense", expense.id, "create", snapshot_instance(expense))
    return expense


def get_recurring_expense(session: Session, expense_id: int) -> RecurringExpense:
    exp = session.get(RecurringExpense, expense_id)
    if exp is None:
        raise ValueError(f"Recurring expense {expense_id} not found")
    return exp


def update_recurring_expense(session: Session, expense_id: int, **kwargs) -> RecurringExpense:
    exp = get_recurring_expense(session, expense_id)
    old_snap = snapshot_instance(exp)
    safe_update(exp, kwargs)
    session.flush()
    changes = diff_instance(old_snap, snapshot_instance(exp))
    if changes:
        record_change(session, "recurring_expense", exp.id, "update", changes)
    return exp


def list_recurring_expenses(session: Session, *, active_only: bool = True) -> list[RecurringExpense]:
    query = session.query(RecurringExpense)
    if active_only:
        query = query.filter(RecurringExpense.active.is_(True))
    return query.order_by(RecurringExpense.name).all()


def generate_transactions(session: Session) -> list[Transaction]:
    """Generate pending transactions for all active recurring expenses due now."""
    today = date.today()
    generated = []
    expenses = session.query(RecurringExpense).filter(RecurringExpense.active.is_(True)).all()

    for exp in expenses:
        if exp.end_date and exp.end_date < today:
            continue
        next_due = _next_due_date(exp)
        if next_due and next_due <= today:
            tx = Transaction(
                amount=exp.amount, currency=exp.currency,
                property_id=exp.property_id, vendor_id=exp.vendor_id,
                category=exp.category, description=f"Recurring: {exp.name}",
                date=next_due, source="recurring_expense",
            )
            session.add(tx)
            exp.last_generated = next_due
            generated.append(tx)

    session.flush()
    return generated


def _step_date(d: date, exp: RecurringExpense) -> date:
    """Advance a date forward by one occurrence of this expense's frequency."""
    match exp.frequency:
        case ExpenseFrequency.WEEKLY:
            return d + timedelta(weeks=1)
        case ExpenseFrequency.BIWEEKLY:
            return d + timedelta(weeks=2)
        case ExpenseFrequency.MONTHLY:
            return add_months(d, 1)
        case ExpenseFrequency.QUARTERLY:
            return add_months(d, 3)
        case ExpenseFrequency.ANNUAL:
            return add_months(d, 12)
        case ExpenseFrequency.CUSTOM_WEEKS:
            return d + timedelta(weeks=exp.interval_count or 1)
        case ExpenseFrequency.CUSTOM_MONTHS:
            return add_months(d, exp.interval_count or 1)
        case _:
            raise ValueError(f"Unknown recurring expense frequency: {exp.frequency}")


def _next_due_date(exp: RecurringExpense) -> date | None:
    """Calculate when this expense is next due."""
    if exp.last_generated is None:
        return exp.start_date
    return _step_date(exp.last_generated, exp)


def generate_upcoming_appointments(session: Session, horizon_days: int = 180) -> list:
    """Ensure calendar Appointments exist for active recurring expenses out to
    the given horizon, so a recurring service (e.g. pest control every 2
    months) is visible on the calendar ahead of time — not just after its
    budget transaction has already been auto-generated for it.

    Dedups by (recurring_expense_id, date) so it's safe to call repeatedly.
    """
    from mihomes.models.appointment import Appointment
    from mihomes.services.appointment import create_appointment

    today = date.today()
    horizon_end = today + timedelta(days=horizon_days)
    created = []

    expenses = session.query(RecurringExpense).filter(RecurringExpense.active.is_(True)).all()
    for exp in expenses:
        if exp.end_date and exp.end_date < today:
            continue

        exp_horizon_end = min(horizon_end, exp.end_date) if exp.end_date else horizon_end

        existing_dates = {
            a.date for a in session.query(Appointment)
            .filter(Appointment.recurring_expense_id == exp.id)
            .all()
        }

        cursor = exp.start_date if exp.last_generated is None else _step_date(exp.last_generated, exp)
        while cursor <= exp_horizon_end:
            if cursor >= today and cursor not in existing_dates:
                appt = create_appointment(
                    session,
                    title=exp.name,
                    property_id_or_slug=str(exp.property_id),
                    appt_date=cursor,
                    vendor_id=exp.vendor_id,
                    appointment_type="vendor_visit",
                    recurring_expense_id=exp.id,
                )
                created.append(appt)
                existing_dates.add(cursor)
            cursor = _step_date(cursor, exp)

    return created
