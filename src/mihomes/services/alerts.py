"""Alert service — generate and manage alerts."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.task import Task, TaskStatus
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus


def generate_alerts(session: Session) -> int:
    """Scan for alert conditions and create/update alerts. Returns count of new alerts."""
    count = 0
    count += _check_overdue_tasks(session)
    count += _check_critical_issues(session)
    count += _check_low_inventory(session)
    # Also check expirations (contracts, insurance, warranties)
    from mihomes.services.automation import generate_expiration_alerts
    count += generate_expiration_alerts(session, days_ahead=30)
    return count


def _check_overdue_tasks(session: Session) -> int:
    """Create alerts for overdue tasks."""
    overdue = session.query(Task).filter(
        Task.due_date < date.today(),
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    ).all()

    count = 0
    for task in overdue:
        # Check if alert already exists for this task
        existing = session.query(Alert).filter(
            Alert.source_entity_type == "task",
            Alert.source_entity_id == task.id,
            Alert.alert_type == "overdue_task",
            Alert.status != AlertStatus.RESOLVED,
        ).first()
        if existing:
            continue

        days_overdue = (date.today() - task.due_date).days
        severity = AlertSeverity.HIGH if days_overdue > 7 else AlertSeverity.MEDIUM

        alert = Alert(
            alert_type="overdue_task",
            source_entity_type="task",
            source_entity_id=task.id,
            severity=severity,
            message=f"Task '{task.title}' is {days_overdue} days overdue (due: {task.due_date})",
        )
        session.add(alert)
        count += 1

    session.flush()
    return count


def _check_critical_issues(session: Session) -> int:
    """Create alerts for unresolved critical/high issues older than 3 days."""
    threshold = date.today() - timedelta(days=3)
    issues = session.query(Issue).filter(
        Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        Issue.severity.in_([IssueSeverity.CRITICAL, IssueSeverity.HIGH]),
        Issue.created_at <= datetime(threshold.year, threshold.month, threshold.day, tzinfo=timezone.utc),
    ).all()

    count = 0
    for issue in issues:
        existing = session.query(Alert).filter(
            Alert.source_entity_type == "issue",
            Alert.source_entity_id == issue.id,
            Alert.alert_type == "unresolved_issue",
            Alert.status != AlertStatus.RESOLVED,
        ).first()
        if existing:
            continue

        alert = Alert(
            alert_type="unresolved_issue",
            source_entity_type="issue",
            source_entity_id=issue.id,
            severity=AlertSeverity.HIGH if issue.severity == IssueSeverity.CRITICAL else AlertSeverity.MEDIUM,
            message=f"Issue '{issue.title}' ({issue.severity.value}) unresolved for 3+ days",
        )
        session.add(alert)
        count += 1

    session.flush()
    return count


def _check_low_inventory(session: Session) -> int:
    """Create alerts for consumables that are low or out of stock."""
    from mihomes.models.consumable import Consumable, ConsumableStatus

    items = session.query(Consumable).filter(
        Consumable.status.in_([ConsumableStatus.LOW, ConsumableStatus.OUT])
    ).all()

    count = 0
    for item in items:
        existing = session.query(Alert).filter(
            Alert.source_entity_type == "consumable",
            Alert.source_entity_id == item.id,
            Alert.alert_type == "low_inventory",
            Alert.status != AlertStatus.RESOLVED,
        ).first()
        if existing:
            continue

        severity = AlertSeverity.HIGH if item.status == ConsumableStatus.OUT else AlertSeverity.MEDIUM
        status_label = "out of stock" if item.status == ConsumableStatus.OUT else "running low"
        alert = Alert(
            alert_type="low_inventory",
            source_entity_type="consumable",
            source_entity_id=item.id,
            severity=severity,
            message=f"Consumable '{item.name}' is {status_label} — reorder needed",
        )
        session.add(alert)
        count += 1

    # Auto-resolve stale inventory alerts for items that are back in stock
    stale_ids = session.query(Alert.source_entity_id).filter(
        Alert.alert_type == "low_inventory",
        Alert.source_entity_type == "consumable",
        Alert.status != AlertStatus.RESOLVED,
    ).all()
    stale_consumable_ids = {row[0] for row in stale_ids}
    if stale_consumable_ids:
        restocked = session.query(Consumable).filter(
            Consumable.id.in_(stale_consumable_ids),
            Consumable.status == ConsumableStatus.OK,
        ).all()
        for item in restocked:
            stale_alerts = session.query(Alert).filter(
                Alert.alert_type == "low_inventory",
                Alert.source_entity_type == "consumable",
                Alert.source_entity_id == item.id,
                Alert.status != AlertStatus.RESOLVED,
            ).all()
            for alert in stale_alerts:
                alert.status = AlertStatus.RESOLVED

    session.flush()
    return count


def list_alerts(
    session: Session,
    *,
    status: AlertStatus | None = None,
    include_snoozed: bool = False,
) -> list[Alert]:
    query = session.query(Alert)
    if status:
        query = query.filter(Alert.status == status)
    else:
        query = query.filter(Alert.status != AlertStatus.RESOLVED)
    if not include_snoozed:
        now = datetime.now(timezone.utc)
        query = query.filter(
            (Alert.snoozed_until == None) | (Alert.snoozed_until <= now)
        )
    return query.order_by(Alert.severity, Alert.created_at.desc()).all()


def snooze_alert(session: Session, alert_id: int, days: int) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise ValueError(f"Alert {alert_id} not found")
    alert.snoozed_until = datetime.now(timezone.utc) + timedelta(days=days)
    alert.status = AlertStatus.ACKNOWLEDGED
    session.flush()
    return alert


def acknowledge_alert(session: Session, alert_id: int) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise ValueError(f"Alert {alert_id} not found")
    alert.status = AlertStatus.ACKNOWLEDGED
    session.flush()
    return alert
