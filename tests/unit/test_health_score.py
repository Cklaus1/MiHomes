"""Regression tests for property health scoring — H17 alert scoping."""

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
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
