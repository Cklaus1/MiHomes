"""R5.3 finance-math fixes: M2, M3, M4, M6.

Each test is a regression: it fails against the pre-fix code and passes after.
"""

from datetime import date

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
    """An annual budget of 1200 counts as 100/month, so 150 spent is over.

    **Booked on `today`, not `month_start + 2`.** `weekly_report:177` filters
    `Transaction.date <= today`, so a transaction dated later in the current month is simply
    not counted — and on the 1st or 2nd, `month_start + 2` *is* later. The test then measured
    a spend of 0 and failed its own comment.

    Found on 2026-09-01. `today` is the only anchor guaranteed both inside the current month
    and not in the future, whatever the date.
    """
    today = date.today()
    month_start = today.replace(day=1)
    set_budget(session, prop.slug, "utilities", BudgetPeriod.ANNUAL,
               1200.0, month_start)
    # spend 150 this month — under the raw 1200 but over the 100/mo prorated share
    session.add(Transaction(amount=150.0, currency="USD", property_id=prop.id,
                            category="utilities", description="x",
                            date=today))
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
    """Only 3 months of history -> monthly_avg = total/3, not total/12.

    **The seeding steps calendar months, not 30-day intervals.** It used
    `today - timedelta(days=30 * m + 1)`, which is not one-per-month: from the 31st of a month
    those three offsets land in only *two* calendar months, and `forecast` divides by the count
    of distinct months it actually finds. The test then measured 900/2 = 450 and failed against
    its own comment.

    Found on 2026-08-31, fixed by anchoring each month on day 15 — **which was itself
    date-dependent and failed the next morning.** `financial_report:109` filters
    `Transaction.date <= today`, so on any date before the 15th the *current* month's row is in
    the future and is dropped: 600 over a 3-month span = 200, not 300.

    The current month must therefore be anchored on `today` — the only day guaranteed to be
    both in the month and not in the future. Earlier months keep day 15, comfortably clear of
    either boundary whatever the month length.

    Verified across the dates that break each variant: the 1st, 2nd, 14th, 15th, 28th–31st.
    """
    today = date.today()
    # One transaction in each of the last three calendar months. The current month uses
    # `today`; older months use mid-month, so no anchor can slip into a neighbouring month or
    # past the `<= today` cutoff.
    for m in range(3):
        year, month = today.year, today.month - m
        while month < 1:
            month += 12
            year -= 1
        when = today if m == 0 else date(year, month, 15)
        session.add(Transaction(amount=300.0, currency="USD", property_id=prop.id,
                                category="misc", description="x",
                                date=when))
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
