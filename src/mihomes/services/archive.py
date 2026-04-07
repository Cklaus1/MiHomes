"""Archive service — data retention and archival for high-volume tables."""

from datetime import datetime, timezone, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from mihomes.models.audit_log import AuditLog
from mihomes.models.ai_conversation import AIConversation
from mihomes.services.config_service import get_config

_ARCHIVABLE_TABLES = {
    "audit_log": {
        "retention_key": "retention.audit_years",
        "default_years": 2,
        "archive_table": "audit_log_archive",
        "description": "Audit log entries",
    },
    "ai_conversations": {
        "retention_key": "retention.ai_years",
        "default_years": 1,
        "archive_table": "ai_conversations_archive",
        "description": "AI conversation history",
    },
}


def _retention_cutoff(session: Session, key: str, default_years: int) -> datetime:
    val = get_config(session, key)
    years = int(val) if val else default_years
    return datetime.now(timezone.utc) - timedelta(days=years * 365)


def get_stats(session: Session) -> list[dict]:
    """Return row counts for archivable tables — active and already archived."""
    results = []

    for table_name, cfg in _ARCHIVABLE_TABLES.items():
        cutoff = _retention_cutoff(session, cfg["retention_key"], cfg["default_years"])
        archive_table = cfg["archive_table"]
        years = get_config(session, cfg["retention_key"]) or str(cfg["default_years"])

        # Total active rows
        total = session.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar() or 0

        # Rows eligible for archival (older than retention cutoff)
        if table_name == "audit_log":
            eligible = session.query(AuditLog).filter(
                AuditLog.timestamp < cutoff
            ).count()
        else:
            eligible = session.query(AIConversation).filter(
                AIConversation.created_at < cutoff
            ).count()

        # Already archived
        try:
            archived = session.execute(
                text(f"SELECT COUNT(*) FROM {archive_table}")
            ).scalar() or 0
        except Exception:
            archived = 0

        results.append({
            "table": table_name,
            "description": cfg["description"],
            "active_rows": total,
            "eligible_to_archive": eligible,
            "already_archived": archived,
            "retention_years": int(years),
            "cutoff_date": cutoff.date(),
        })

    return results


def run_archival(session: Session, dry_run: bool = False) -> dict:
    """Move rows older than retention window into archive tables.

    Returns counts of rows archived per table.
    """
    results = {}

    # Audit log archival
    audit_cfg = _ARCHIVABLE_TABLES["audit_log"]
    cutoff = _retention_cutoff(session, audit_cfg["retention_key"], audit_cfg["default_years"])
    old_audit = session.query(AuditLog).filter(AuditLog.timestamp < cutoff).all()

    if old_audit and not dry_run:
        session.execute(text(
            "INSERT INTO audit_log_archive "
            "(id, timestamp, entity_type, entity_id, action, changes, actor, archived_at) "
            "SELECT id, timestamp, entity_type, entity_id, action, changes, actor, "
            f"'{datetime.now(timezone.utc).isoformat()}' "
            f"FROM audit_log WHERE timestamp < '{cutoff.isoformat()}'"
        ))
        session.execute(text(
            f"DELETE FROM audit_log WHERE timestamp < '{cutoff.isoformat()}'"
        ))
    results["audit_log"] = len(old_audit)

    # AI conversations archival
    ai_cfg = _ARCHIVABLE_TABLES["ai_conversations"]
    cutoff = _retention_cutoff(session, ai_cfg["retention_key"], ai_cfg["default_years"])
    old_ai = session.query(AIConversation).filter(AIConversation.created_at < cutoff).all()

    if old_ai and not dry_run:
        session.execute(text(
            "INSERT INTO ai_conversations_archive "
            "(id, session_id, role, user_message, ai_response, context_summary, "
            "tokens_used, provider, model, created_at, updated_at, archived_at) "
            "SELECT id, session_id, role, user_message, ai_response, context_summary, "
            "tokens_used, provider, model, created_at, updated_at, "
            f"'{datetime.now(timezone.utc).isoformat()}' "
            f"FROM ai_conversations WHERE created_at < '{cutoff.isoformat()}'"
        ))
        session.execute(text(
            f"DELETE FROM ai_conversations WHERE created_at < '{cutoff.isoformat()}'"
        ))
    results["ai_conversations"] = len(old_ai)

    return results
