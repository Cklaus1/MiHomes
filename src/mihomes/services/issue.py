"""Issue service — CRUD with lifecycle management."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.property import Property
from mihomes.models.space import Space
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier


def create_issue(
    session: Session,
    title: str,
    property_id_or_slug: str,
    *,
    severity: IssueSeverity = IssueSeverity.MEDIUM,
    description: str | None = None,
    space_id_or_slug: str | None = None,
    photos: list[str] | None = None,
    slug: str | None = None,
) -> Issue:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    space_id = None
    if space_id_or_slug:
        space = resolve_identifier(session, Space, space_id_or_slug)
        space_id = space.id
    slug = ensure_unique_slug(session, Issue, slug or generate_slug(title))
    issue = Issue(
        title=title, slug=slug, description=description,
        property_id=prop.id, space_id=space_id, severity=severity, photos=photos,
    )
    session.add(issue)
    session.flush()
    record_change(session, "issue", issue.id, "create", snapshot_instance(issue))
    return issue


def list_issues(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    severity: IssueSeverity | None = None,
    open_only: bool = False,
    resolved_only: bool = False,
) -> list[Issue]:
    query = session.query(Issue)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Issue.property_id == prop.id)
    if severity:
        query = query.filter(Issue.severity == severity)
    if open_only:
        query = query.filter(Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]))
    if resolved_only:
        query = query.filter(Issue.status.in_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]))
    return query.order_by(Issue.severity, Issue.created_at.desc()).all()


def get_issue(session: Session, id_or_slug: str) -> Issue:
    return resolve_identifier(session, Issue, id_or_slug)


def update_issue(session: Session, id_or_slug: str, **kwargs) -> Issue:
    issue = resolve_identifier(session, Issue, id_or_slug)
    old_snap = snapshot_instance(issue)
    if "title" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Issue, generate_slug(kwargs["title"]), exclude_id=issue.id)
    safe_update(issue, kwargs)
    session.flush()
    new_snap = snapshot_instance(issue)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "issue", issue.id, "update", changes)
    return issue


def resolve_issue(session: Session, id_or_slug: str, notes: str | None = None) -> Issue:
    issue = resolve_identifier(session, Issue, id_or_slug)
    if issue.status == IssueStatus.VERIFIED:
        raise ValueError("Issue is already verified and cannot be moved back to resolved")
    old_snap = snapshot_instance(issue)
    issue.status = IssueStatus.RESOLVED
    issue.resolved_at = datetime.now(timezone.utc)
    issue.resolution_notes = notes
    session.flush()
    new_snap = snapshot_instance(issue)
    changes = diff_instance(old_snap, new_snap)
    record_change(session, "issue", issue.id, "update", changes)

    # Auto-resolve any alerts for this issue
    from mihomes.models.alert import Alert, AlertStatus
    stale_alerts = session.query(Alert).filter(
        Alert.source_entity_type == "issue",
        Alert.source_entity_id == issue.id,
        Alert.status != AlertStatus.RESOLVED,
    ).all()
    for alert in stale_alerts:
        alert.status = AlertStatus.RESOLVED
    if stale_alerts:
        session.flush()

    return issue


def delete_issue(session: Session, id_or_slug: str) -> str:
    from mihomes.models.alert import Alert, AlertStatus
    issue = resolve_identifier(session, Issue, id_or_slug)
    name = issue.title
    record_change(session, "issue", issue.id, "delete", snapshot_instance(issue))
    # Resolve any alerts referencing this issue before deleting it
    stale_alerts = session.query(Alert).filter(
        Alert.source_entity_type == "issue",
        Alert.source_entity_id == issue.id,
        Alert.status != AlertStatus.RESOLVED,
    ).all()
    for alert in stale_alerts:
        alert.status = AlertStatus.RESOLVED
    session.delete(issue)
    session.flush()
    return name
