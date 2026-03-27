"""Budget service — budget management and expense tracking."""

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.budget import Budget, BudgetPeriod, Transaction
from mihomes.models.property import Property
from mihomes.models.vendor import Vendor
from mihomes.services.audit import record_change, snapshot_instance
from mihomes.services.slug import resolve_identifier


def set_budget(
    session: Session,
    property_id_or_slug: str,
    category: str,
    period: BudgetPeriod,
    amount: float,
    period_start: date,
    currency: str = "USD",
) -> Budget:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    # Upsert: update if exists for same property/category/period/start
    existing = session.query(Budget).filter(
        Budget.property_id == prop.id,
        Budget.category == category,
        Budget.period == period,
        Budget.period_start == period_start,
    ).first()
    if existing:
        existing.amount = amount
        existing.currency = currency
        session.flush()
        record_change(session, "budget", existing.id, "update", {"amount": {"old": None, "new": amount}})
        return existing
    budget = Budget(
        property_id=prop.id, category=category, period=period,
        period_start=period_start, amount=amount, currency=currency,
    )
    session.add(budget)
    session.flush()
    record_change(session, "budget", budget.id, "create", snapshot_instance(budget))
    return budget


def add_transaction(
    session: Session,
    amount: float,
    property_id_or_slug: str,
    category: str,
    tx_date: date,
    *,
    vendor_id_or_slug: str | None = None,
    description: str | None = None,
    currency: str = "USD",
    notes: str | None = None,
    source: str = "manual",
) -> Transaction:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    vendor_id = None
    if vendor_id_or_slug:
        vendor = resolve_identifier(session, Vendor, vendor_id_or_slug)
        vendor_id = vendor.id
    tx = Transaction(
        amount=amount, currency=currency, property_id=prop.id,
        vendor_id=vendor_id, category=category, description=description,
        date=tx_date, source=source, notes=notes,
    )
    session.add(tx)
    session.flush()
    record_change(session, "transaction", tx.id, "create", snapshot_instance(tx))
    return tx


def list_transactions(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[Transaction]:
    query = session.query(Transaction)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Transaction.property_id == prop.id)
    if category:
        query = query.filter(Transaction.category == category)
    if date_from:
        query = query.filter(Transaction.date >= date_from)
    if date_to:
        query = query.filter(Transaction.date <= date_to)
    return query.order_by(Transaction.date.desc()).all()


def get_budget_report(
    session: Session,
    property_id_or_slug: str,
    period_start: date,
    period_end: date,
) -> list[dict]:
    """Get budget vs actual spending by category for a property and period."""
    prop = resolve_identifier(session, Property, property_id_or_slug)

    # Get budgets for this property
    budgets = session.query(Budget).filter(
        Budget.property_id == prop.id,
        Budget.period_start >= period_start,
        Budget.period_start < period_end,
    ).all()

    # Get spending by category
    spending = session.query(
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
    ).filter(
        Transaction.property_id == prop.id,
        Transaction.date >= period_start,
        Transaction.date < period_end,
    ).group_by(Transaction.category).all()

    spending_map = {row.category: row.total for row in spending}

    # Build report
    report = []
    categories_seen = set()
    for b in budgets:
        spent = spending_map.get(b.category, 0.0)
        pct = (spent / b.amount * 100) if b.amount > 0 else 0
        report.append({
            "category": b.category,
            "budgeted": b.amount,
            "spent": spent,
            "remaining": b.amount - spent,
            "pct_used": round(pct, 1),
            "currency": b.currency,
        })
        categories_seen.add(b.category)

    # Add unbudgeted spending
    for cat, total in spending_map.items():
        if cat not in categories_seen:
            report.append({
                "category": cat,
                "budgeted": 0.0,
                "spent": total,
                "remaining": -total,
                "pct_used": 0,
                "currency": prop.currency,
            })

    return sorted(report, key=lambda r: r["category"])
