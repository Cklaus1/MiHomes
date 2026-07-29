"""R5.3 finance-math fixes: M2, M3, M4, M6.

Each test is a regression: it fails against the pre-fix code and passes after.
"""

from datetime import date, timedelta

import pytest

from mihomes.models.audit_log import AuditLog
from mihomes.models.budget import Budget, BudgetPeriod, Transaction
from mihomes.models.property import Property, PropertyType
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.services.budget import set_budget
from mihomes.services.financial_report import forecast
from mihomes.services.weekly_report import generate as generate_weekly_report
from mihomes.services.work_order import complete


@pytest.fixture
def prop(session):
    p = Property(name="Finance House", slug="finance-house",
                 property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


# ── M2: actual_cost=0.0 must not fall back to estimated_cost ──────────────────

def test_m2_zero_actual_cost_not_replaced_by_estimate(session, prop):
    """A $0 warranty completion must book $0, not the estimate."""
    wo = WorkOrder(title="Warranty fix", slug="warranty-fix", property_id=prop.id,
                   status=WorkOrderStatus.APPROVED, estimated_cost=250.0)
    session.add(wo)
    session.flush()

    complete(session, wo.slug, actual_cost=0.0)
    session.flush()

    # $0 work books NO phantom transaction (cost > 0 guard), and crucially the
    # actual_cost of 0.0 is respected rather than being overwritten by 250.
    assert wo.actual_cost == 0.0
    txs = session.query(Transaction).filter(
        Transaction.work_order_id == wo.id
    ).all()
    total = sum(t.amount for t in txs)
    assert total == 0.0, f"expected no phantom estimate booked, got {total}"


# ── M3: quarterly/annual budgets prorated to a monthly figure ─────────────────

def test_m3_annual_budget_prorated_to_month(session, prop):
    """An annual budget of 1200 counts as 100/month, so 150 spent is over."""
    today = date.today()
    month_start = today.replace(day=1)
    set_budget(session, prop.slug, "utilities", BudgetPeriod.ANNUAL,
               1200.0, month_start)
    # spend 150 this month — under the raw 1200 but over the 100/mo prorated share
    session.add(Transaction(amount=150.0, currency="USD", property_id=prop.id,
                            category="utilities", description="x",
                            date=month_start + timedelta(days=2)))
    session.flush()

    report = generate_weekly_report(session)
    row = next(r for r in report["budget"] if r["slug"] == prop.slug)
    # prorated budget is 100, spent is 150 -> over budget
    assert row["budgeted_mtd"] == pytest.approx(100.0)
    assert row["over_budget"] is True


def test_m3_quarterly_budget_prorated(session, prop):
    month_start = date.today().replace(day=1)
    set_budget(session, prop.slug, "landscaping", BudgetPeriod.QUARTERLY,
               300.0, month_start)
    session.flush()
    report = generate_weekly_report(session)
    row = next(r for r in report["budget"] if r["slug"] == prop.slug)
    assert row["budgeted_mtd"] == pytest.approx(100.0)  # 300 / 3


# ── M4: forecast divides by real months of history, survives Feb 29 ───────────

def test_m4_forecast_divides_by_actual_history(session, prop):
    """Only 3 months of history -> monthly_avg = total/3, not total/12."""
    today = date.today()
    # three transactions, one per month for the last 3 months
    for m in range(3):
        session.add(Transaction(amount=300.0, currency="USD", property_id=prop.id,
                                category="misc", description="x",
                                date=today - timedelta(days=30 * m + 1)))
    session.flush()

    result = forecast(session, prop.slug, months=6)
    # 900 total over 3 months of history -> 300/month, NOT 900/12 = 75
    assert result["monthly_average"] == pytest.approx(300.0, abs=1.0)


# ── M6: budget update audit captures the OLD value, not old==new ──────────────

def test_m6_budget_update_audit_old_value(session, prop):
    month_start = date.today().replace(day=1)
    set_budget(session, prop.slug, "cleaning", BudgetPeriod.MONTHLY,
               100.0, month_start)
    session.flush()
    b = session.query(Budget).filter(Budget.category == "cleaning").one()

    # update the same budget (upsert path) to a new amount
    set_budget(session, prop.slug, "cleaning", BudgetPeriod.MONTHLY,
               250.0, month_start)
    session.flush()

    update_entry = (
        session.query(AuditLog)
        .filter(AuditLog.entity_type == "budget",
                AuditLog.entity_id == b.id,
                AuditLog.action == "update")
        .one()
    )
    changes = update_entry.changes
    assert changes["amount"]["old"] == 100.0, changes
    assert changes["amount"]["new"] == 250.0, changes
