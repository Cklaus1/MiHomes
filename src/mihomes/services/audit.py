"""Audit log service — record changes to any entity."""

from datetime import date, datetime
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


def resolve_actor() -> str:
    """The acting identity for an audit row, from the request/CLI context.

    **This is F6's fix, and it is one function rather than 73 edits.** `AuditLog.actor` defaulted
    to the literal `"admin"`, so every one of the 73 `record_change` call sites across 20 services
    recorded a fictional actor — "the audit work is threading a real actor through an existing
    table". Those services do not know who is acting and should not have to: the *request* knows,
    and `mihomes.tenancy.current_user` is already bound per request by `require_authenticated`
    (SPEC-003 G0) and per command by the CLI callback. Reading it here threads the real actor
    through every existing call site without changing any of them.

    Falls back to `"system"` — not `"admin"` — for genuinely unattended paths (watchdog respawns,
    scheduled jobs, migrations). `"system"` is *true* where `"admin"` was a guess, and it is
    visibly distinct in the log from a real user id, so an unattributed write cannot be mistaken
    for a human one.
    """
    from mihomes.tenancy import current_user

    try:
        return str(current_user.get())
    except LookupError:
        # No user context: an unattended path. Fail to an honest label rather than to a name.
        return "system"


def record_change(
    session: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    changes: dict | None = None,
    actor: str | None = None,
) -> AuditLog:
    """Insert an audit log entry.

    `actor=None` means "resolve it from context" (see `resolve_actor`). An explicit value still
    wins, for the paths that genuinely act on someone else's behalf.
    """
    entry = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changes=changes,
        actor=actor if actor is not None else resolve_actor(),
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
