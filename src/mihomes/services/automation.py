"""Automation service — scheduled operations, escalation, digests, reorder alerts."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.asset import Asset, AssetType
from mihomes.models.contract import Contract
from mihomes.models.insurance import InsurancePolicy
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.property import Property
from mihomes.models.task import Task, TaskPriority, TaskStatus


def escalate_overdue_tasks(session: Session, escalate_after_days: int = 7) -> int:
    """Escalate priority of tasks overdue by more than N days."""
    cutoff = date.today() - timedelta(days=escalate_after_days)
    escalated = 0

    tasks = session.query(Task).filter(
        Task.due_date <= cutoff,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        Task.priority != TaskPriority.URGENT,
    ).all()

    priority_order = [TaskPriority.LOW, TaskPriority.MEDIUM, TaskPriority.HIGH, TaskPriority.URGENT]

    for task in tasks:
        idx = priority_order.index(task.priority)
        if idx < len(priority_order) - 1:
            task.priority = priority_order[idx + 1]
            escalated += 1

    session.flush()
    return escalated


def generate_expiration_alerts(session: Session, days_ahead: int = 30) -> int:
    """Generate alerts for items expiring within N days."""
    cutoff = date.today() + timedelta(days=days_ahead)
    today = date.today()
    count = 0

    # Contracts expiring
    contracts = session.query(Contract).filter(
        Contract.end_date != None,
        Contract.end_date <= cutoff,
        Contract.end_date >= today,
    ).all()
    for c in contracts:
        if not _alert_exists(session, "expiring_contract", c.id):
            days_left = (c.end_date - today).days
            renew = " (auto-renew)" if c.auto_renew else ""
            session.add(Alert(
                alert_type="expiring_contract", source_entity_type="contract",
                source_entity_id=c.id, severity=AlertSeverity.MEDIUM,
                message=f"Contract with {c.vendor.company_name} expires in {days_left} days{renew}",
            ))
            count += 1

    # Insurance expiring
    policies = session.query(InsurancePolicy).filter(
        InsurancePolicy.renewal_date != None,
        InsurancePolicy.renewal_date <= cutoff,
        InsurancePolicy.renewal_date >= today,
    ).all()
    for p in policies:
        if not _alert_exists(session, "expiring_insurance", p.id):
            days_left = (p.renewal_date - today).days
            session.add(Alert(
                alert_type="expiring_insurance", source_entity_type="insurance",
                source_entity_id=p.id, severity=AlertSeverity.HIGH,
                message=f"Insurance policy ({p.carrier}, {p.insurance_type.value}) renews in {days_left} days",
            ))
            count += 1

    # Asset warranties expiring
    assets = session.query(Asset).filter(
        Asset.warranty_expires != None,
        Asset.warranty_expires <= cutoff,
        Asset.warranty_expires >= today,
        Asset.active == True,
    ).all()
    for a in assets:
        if not _alert_exists(session, "expiring_warranty", a.id):
            days_left = (a.warranty_expires - today).days
            session.add(Alert(
                alert_type="expiring_warranty", source_entity_type="asset",
                source_entity_id=a.id, severity=AlertSeverity.LOW,
                message=f"Warranty for '{a.name}' at {a.property.name} expires in {days_left} days",
            ))
            count += 1

    session.flush()
    return count


def generate_daily_digest(session: Session, property_slug: str | None = None) -> dict:
    """Generate a daily digest summary."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    week_end = today + timedelta(days=7)

    # Tasks due today
    tasks_today_q = session.query(Task).filter(
        Task.due_date == today,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    if property_slug:
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, property_slug)
        tasks_today_q = tasks_today_q.filter(Task.property_id == prop.id)
    tasks_today = tasks_today_q.all()

    # Overdue tasks
    overdue_q = session.query(Task).filter(
        Task.due_date < today,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    if property_slug:
        overdue_q = overdue_q.filter(Task.property_id == prop.id)
    overdue = overdue_q.all()

    # Open critical/high issues
    issues_q = session.query(Issue).filter(
        Issue.severity.in_([IssueSeverity.CRITICAL, IssueSeverity.HIGH]),
        Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
    )
    if property_slug:
        issues_q = issues_q.filter(Issue.property_id == prop.id)
    critical_issues = issues_q.all()

    # Active alerts
    from mihomes.services.alerts import list_alerts
    alerts = list_alerts(session)

    return {
        "date": today.isoformat(),
        "tasks_due_today": [{"title": t.title, "property": t.property.name, "priority": t.priority.value} for t in tasks_today],
        "overdue_tasks": [{"title": t.title, "property": t.property.name, "days_overdue": (today - t.due_date).days} for t in overdue],
        "critical_issues": [{"title": i.title, "property": i.property.name, "severity": i.severity.value} for i in critical_issues],
        "alert_count": len(alerts),
    }


def format_digest_brief(digest: dict) -> str:
    """Format a digest as plain text for cron/email output."""
    lines = [f"MiHomes Daily Digest — {digest['date']}", ""]

    if digest["overdue_tasks"]:
        lines.append(f"OVERDUE ({len(digest['overdue_tasks'])}):")
        for t in digest["overdue_tasks"]:
            lines.append(f"  ! {t['title']} @ {t['property']} ({t['days_overdue']}d overdue)")
        lines.append("")

    if digest["tasks_due_today"]:
        lines.append(f"DUE TODAY ({len(digest['tasks_due_today'])}):")
        for t in digest["tasks_due_today"]:
            lines.append(f"  - {t['title']} @ {t['property']} [{t['priority']}]")
        lines.append("")

    if digest["critical_issues"]:
        lines.append(f"CRITICAL/HIGH ISSUES ({len(digest['critical_issues'])}):")
        for i in digest["critical_issues"]:
            lines.append(f"  ! [{i['severity']}] {i['title']} @ {i['property']}")
        lines.append("")

    if digest["alert_count"]:
        lines.append(f"ALERTS: {digest['alert_count']} pending — run 'mihomes alerts'")

    if not any([digest["overdue_tasks"], digest["tasks_due_today"], digest["critical_issues"]]):
        lines.append("All clear — no urgent items today.")

    return "\n".join(lines)


def _alert_exists(session: Session, alert_type: str, source_id: int) -> bool:
    return session.query(Alert).filter(
        Alert.alert_type == alert_type,
        Alert.source_entity_id == source_id,
        Alert.status != AlertStatus.RESOLVED,
    ).first() is not None
