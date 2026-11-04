"""Alert service — generate and manage alerts."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.task import Task, TaskStatus
from mihomes.services.localtime import today as local_today
from mihomes.services.slug import get_by_id


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


def _utc_naive(dt: datetime | None) -> datetime | None:
    """Comparable form of a timestamp: the columns are naive UTC once read back, but an object
    still in the session can hold the aware value its Python default produced."""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _already_alerted(
    session: Session, alert_type: str, source_type: str, source, *, occurrence_after: date | None = None
) -> bool:
    """Whether `source` must not get a new `alert_type` alert.

    True while an alert is open. Also true when one was resolved **after the source last
    changed**: Resolve in the web app means "dealt with", so the hourly `jobs alerts` must not
    raise the same thing again at the next :23. The source changing (a task reopened, an issue
    re-escalated, stock edited) makes it eligible again.

    `occurrence_after` scopes that to the current occurrence for overdue tasks: an alert created
    on or before the task's current due date belonged to an earlier due date, so moving the due
    date out and missing it again still raises a fresh alert.
    """
    changed = _utc_naive(source.updated_at or source.created_at)
    alerts = session.query(Alert).filter(
        Alert.source_entity_type == source_type,
        Alert.source_entity_id == source.id,
        Alert.alert_type == alert_type,
    ).all()
    for alert in alerts:
        if alert.status != AlertStatus.RESOLVED:
            return True
        if occurrence_after is not None and _utc_naive(alert.created_at).date() <= occurrence_after:
            continue
        resolved_at = _utc_naive(alert.updated_at or alert.created_at)
        if changed is None or resolved_at >= changed:
            return True
    return False


def resolve_cleared_alerts(session: Session) -> int:
    """Resolve overdue-task and unresolved-issue alerts whose cause is gone. Returns the count.

    The generators only ever *open* these alerts, so before `jobs alerts` ran them on a schedule
    a completed task stayed flagged until someone dismissed it by hand. Cleared means the source
    row was deleted, or it no longer meets the condition that raised the alert. Acknowledged and
    snoozed alerts are resolved too: the problem they were about no longer exists.
    (Low-inventory alerts already clear themselves on restock in `_check_low_inventory`.)
    """
    today = local_today()
    resolved = 0

    def _open(alert_type: str) -> list[Alert]:
        return session.query(Alert).filter(
            Alert.alert_type == alert_type,
            Alert.status != AlertStatus.RESOLVED,
        ).all()

    task_alerts = _open("overdue_task")
    if task_alerts:
        tasks = {t.id: t for t in session.query(Task).filter(
            Task.id.in_({a.source_entity_id for a in task_alerts})
        )}
        for alert in task_alerts:
            task = tasks.get(alert.source_entity_id)
            if (
                task is None
                or task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
                or task.due_date is None
                or task.due_date >= today
            ):
                alert.status = AlertStatus.RESOLVED
                resolved += 1

    issue_alerts = _open("unresolved_issue")
    if issue_alerts:
        issues = {i.id: i for i in session.query(Issue).filter(
            Issue.id.in_({a.source_entity_id for a in issue_alerts})
        )}
        for alert in issue_alerts:
            issue = issues.get(alert.source_entity_id)
            if (
                issue is None
                or issue.status in (IssueStatus.RESOLVED, IssueStatus.VERIFIED)
                or issue.severity not in (IssueSeverity.CRITICAL, IssueSeverity.HIGH)
            ):
                alert.status = AlertStatus.RESOLVED
                resolved += 1

    session.flush()
    return resolved


def _check_overdue_tasks(session: Session) -> int:
    """Create alerts for overdue tasks."""
    overdue = session.query(Task).filter(
        Task.due_date < local_today(),
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    ).all()

    count = 0
    for task in overdue:
        if _already_alerted(session, "overdue_task", "task", task, occurrence_after=task.due_date):
            continue

        days_overdue = (local_today() - task.due_date).days
        severity = AlertSeverity.HIGH if days_overdue > 7 else AlertSeverity.MEDIUM

        alert = Alert(
            alert_type="overdue_task",
            source_entity_type="task",
            source_entity_id=task.id,
            property_id=task.property_id,
            severity=severity,
            message=f"Task '{task.title}' is {days_overdue} days overdue (due: {task.due_date})",
        )
        session.add(alert)
        count += 1

    session.flush()
    return count


def _check_critical_issues(session: Session) -> int:
    """Create alerts for unresolved critical/high issues older than 3 days."""
    threshold = local_today() - timedelta(days=3)
    issues = session.query(Issue).filter(
        Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        Issue.severity.in_([IssueSeverity.CRITICAL, IssueSeverity.HIGH]),
        Issue.created_at <= datetime(threshold.year, threshold.month, threshold.day, tzinfo=timezone.utc),
    ).all()

    count = 0
    for issue in issues:
        if _already_alerted(session, "unresolved_issue", "issue", issue):
            continue

        alert = Alert(
            alert_type="unresolved_issue",
            source_entity_type="issue",
            source_entity_id=issue.id,
            property_id=issue.property_id,
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
        if _already_alerted(session, "low_inventory", "consumable", item):
            continue

        severity = AlertSeverity.HIGH if item.status == ConsumableStatus.OUT else AlertSeverity.MEDIUM
        status_label = "out of stock" if item.status == ConsumableStatus.OUT else "running low"
        alert = Alert(
            alert_type="low_inventory",
            source_entity_type="consumable",
            source_entity_id=item.id,
            property_id=item.property_id,
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
            # `== None`, not `is None`: SQLAlchemy overloads `==` on a Column to emit
            # `IS NULL`. `is None` would evaluate to a Python bool and silently drop the
            # predicate, so ruff's E711 suggestion is wrong for a filter expression.
            (Alert.snoozed_until == None) | (Alert.snoozed_until <= now)  # noqa: E711
        )
    return query.order_by(Alert.severity, Alert.created_at.desc()).all()


def snooze_alert(session: Session, alert_id: int, days: int) -> Alert:
    alert = get_by_id(session, Alert, alert_id)
    if alert is None:
        raise ValueError(f"Alert {alert_id} not found")
    alert.snoozed_until = datetime.now(timezone.utc) + timedelta(days=days)
    alert.status = AlertStatus.ACKNOWLEDGED
    session.flush()
    return alert


def acknowledge_alert(session: Session, alert_id: int) -> Alert:
    alert = get_by_id(session, Alert, alert_id)
    if alert is None:
        raise ValueError(f"Alert {alert_id} not found")
    alert.status = AlertStatus.ACKNOWLEDGED
    session.flush()
    return alert
