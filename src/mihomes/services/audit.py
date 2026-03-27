"""Audit log service — record changes to any entity."""

from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy.orm import Session

from mihomes.models.audit_log import AuditLog


def snapshot_instance(instance) -> dict:
    """Convert an ORM instance to a serializable dict."""
    result = {}
    for col in instance.__table__.columns:
        value = getattr(instance, col.name)
        result[col.name] = _serialize_value(value)
    return result


def diff_instance(old: dict, new: dict) -> dict:
    """Compare two snapshots and return changed fields as {field: {old, new}}."""
    changes = {}
    all_keys = set(old.keys()) | set(new.keys())
    for key in all_keys:
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            changes[key] = {"old": old_val, "new": new_val}
    return changes


def record_change(
    session: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    changes: dict | None = None,
    actor: str = "admin",
) -> AuditLog:
    """Insert an audit log entry."""
    entry = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changes=changes,
        actor=actor,
    )
    session.add(entry)
    return entry


def _serialize_value(value):
    """Serialize a value for JSON storage in audit log."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)
