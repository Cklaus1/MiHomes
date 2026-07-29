"""Regression tests for property health scoring — H17 alert scoping, H16 budget window."""

from datetime import date, timedelta

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.budget import Budget, BudgetPeriod, Transaction
from mihomes.models.property import Property, PropertyType
from mihomes.services.health_score import compute_property_health


def _prop(session, slug):
    p = Property(name=slug.title(), slug=slug, property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


def test_alert_scoped_to_property(session):
    """A critical alert on property B must not drag down property A's score."""
    a = _prop(session, "alpha")
    b = _prop(session, "bravo")
    # A high-severity, unacknowledged alert belonging to property B only.
    session.add(Alert(
        alert_type="test", severity=AlertSeverity.CRITICAL,
        status=AlertStatus.GENERATED, message="B is on fire",
        property_id=b.id,
    ))
    session.flush()

    score_a = compute_property_health(session, a.id)
    score_b = compute_property_health(session, b.id)

    assert "alerts" not in score_a.breakdown  # A is untouched by B's alert
    assert score_a.score == 100
    assert score_b.breakdown.get("alerts", 0) < 0  # B takes the deduction
    assert score_b.score < 100


def test_systemwide_alert_hits_every_property(session):
    """A property-less (system-wide) alert deducts from all properties."""
    a = _prop(session, "alpha")
    session.add(Alert(
        alert_type="test", severity=AlertSeverity.CRITICAL,
        status=AlertStatus.GENERATED, message="global outage",
        property_id=None,
    ))
    session.flush()

    score_a = compute_property_health(session, a.id)
    assert score_a.breakdown.get("alerts", 0) < 0
    assert score_a.score < 100


def test_budget_period_window(session):
    """H16 — budget overrun must be measured within the budget's period window.
    Spending from a *prior* period must not count against the current period's
    budget, or an old overrun permanently tanks the health score."""
    p = _prop(session, "windowed")
    today = date.today()
    period_start = today.replace(day=1)  # current month

    session.add(Budget(
        property_id=p.id, category="maintenance",
        period=BudgetPeriod.MONTHLY, period_start=period_start,
        amount=1000.0, currency="USD",
    ))
    # Overspend that happened LAST period — outside the current window.
    session.add(Transaction(
        amount=5000.0, currency="USD", property_id=p.id,
        category="maintenance", description="last period blowout",
        date=period_start - timedelta(days=20),
    ))
    session.flush()

    score = compute_property_health(session, p.id)
    # No in-window spend → no overrun deduction.
    assert "budget" not in score.breakdown, score.breakdown


def test_budget_overrun_within_window_deducts(session):
    """Control: an overrun *inside* the current period still deducts."""
    p = _prop(session, "overrun-now")
    today = date.today()
    period_start = today.replace(day=1)

    session.add(Budget(
        property_id=p.id, category="maintenance",
        period=BudgetPeriod.MONTHLY, period_start=period_start,
        amount=100.0, currency="USD",
    ))
    session.add(Transaction(
        amount=500.0, currency="USD", property_id=p.id,
        category="maintenance", description="in-window overrun",
        date=today,
    ))
    session.flush()

    score = compute_property_health(session, p.id)
    assert score.breakdown.get("budget", 0) < 0
