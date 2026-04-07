"""Tests for archive service."""

from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import text

from mihomes.models.audit_log import AuditLog
from mihomes.models.ai_conversation import AIConversation
from mihomes.services.archive import _retention_cutoff, get_stats, run_archival


def _old_dt(years=3):
    """Return a datetime that is older than any default retention window."""
    return datetime.now(timezone.utc) - timedelta(days=years * 365)


def _recent_dt():
    return datetime.now(timezone.utc) - timedelta(days=10)


def _make_audit(session, timestamp=None):
    entry = AuditLog(
        entity_type="task", entity_id=1, action="create",
        timestamp=timestamp or _old_dt(),
    )
    session.add(entry)
    session.flush()
    return entry


def _make_conversation(session, created_at=None):
    convo = AIConversation(
        session_id="test-session", role="estate_manager",
        user_message="hello", ai_response="hi",
    )
    session.add(convo)
    session.flush()
    if created_at:
        # Override the timestamp directly
        convo.created_at = created_at
        session.flush()
    return convo


class TestRetentionCutoff:
    def test_default_years_used_when_no_config(self, session):
        cutoff = _retention_cutoff(session, "retention.audit_years", 2)
        expected = datetime.now(timezone.utc) - timedelta(days=2 * 365)
        diff = abs((cutoff - expected).total_seconds())
        assert diff < 5  # within 5 seconds

    def test_config_value_overrides_default(self, session):
        from mihomes.services.config_service import set_config
        set_config(session, "retention.audit_years", "5")
        cutoff = _retention_cutoff(session, "retention.audit_years", 2)
        expected = datetime.now(timezone.utc) - timedelta(days=5 * 365)
        diff = abs((cutoff - expected).total_seconds())
        assert diff < 5


class TestGetStats:
    def test_returns_two_table_entries(self, session):
        # Create archive tables for the test
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS audit_log_archive "
            "(id INTEGER, timestamp TEXT, entity_type TEXT, entity_id INTEGER, "
            "action TEXT, changes TEXT, actor TEXT, archived_at TEXT)"
        ))
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS ai_conversations_archive "
            "(id INTEGER, session_id TEXT, role TEXT, user_message TEXT, "
            "ai_response TEXT, context_summary TEXT, tokens_used INTEGER, "
            "provider TEXT, model TEXT, created_at TEXT, updated_at TEXT, archived_at TEXT)"
        ))
        stats = get_stats(session)
        assert len(stats) == 2
        tables = {s["table"] for s in stats}
        assert "audit_log" in tables
        assert "ai_conversations" in tables

    def test_counts_eligible_rows(self, session):
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS audit_log_archive "
            "(id INTEGER, timestamp TEXT, entity_type TEXT, entity_id INTEGER, "
            "action TEXT, changes TEXT, actor TEXT, archived_at TEXT)"
        ))
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS ai_conversations_archive "
            "(id INTEGER, session_id TEXT, role TEXT, user_message TEXT, "
            "ai_response TEXT, context_summary TEXT, tokens_used INTEGER, "
            "provider TEXT, model TEXT, created_at TEXT, updated_at TEXT, archived_at TEXT)"
        ))
        _make_audit(session, timestamp=_old_dt(years=3))  # old — eligible
        _make_audit(session, timestamp=_recent_dt())       # recent — not eligible
        stats = get_stats(session)
        audit_stats = next(s for s in stats if s["table"] == "audit_log")
        assert audit_stats["active_rows"] == 2
        assert audit_stats["eligible_to_archive"] == 1


class TestRunArchival:
    def _setup_archive_tables(self, session):
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS audit_log_archive "
            "(id INTEGER, timestamp TEXT, entity_type TEXT, entity_id INTEGER, "
            "action TEXT, changes TEXT, actor TEXT, archived_at TEXT)"
        ))
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS ai_conversations_archive "
            "(id INTEGER, session_id TEXT, role TEXT, user_message TEXT, "
            "ai_response TEXT, context_summary TEXT, tokens_used INTEGER, "
            "provider TEXT, model TEXT, created_at TEXT, updated_at TEXT, archived_at TEXT)"
        ))

    def test_dry_run_does_not_delete(self, session):
        self._setup_archive_tables(session)
        _make_audit(session, timestamp=_old_dt(years=3))
        results = run_archival(session, dry_run=True)
        assert results["audit_log"] == 1
        # Row still in active table
        count = session.query(AuditLog).count()
        assert count == 1

    def test_dry_run_returns_counts(self, session):
        self._setup_archive_tables(session)
        _make_audit(session, timestamp=_old_dt(years=3))
        _make_audit(session, timestamp=_old_dt(years=3))
        results = run_archival(session, dry_run=True)
        assert results["audit_log"] == 2

    def test_recent_rows_not_archived(self, session):
        self._setup_archive_tables(session)
        _make_audit(session, timestamp=_recent_dt())
        results = run_archival(session, dry_run=True)
        assert results["audit_log"] == 0

    def test_no_rows_returns_zero_counts(self, session):
        self._setup_archive_tables(session)
        results = run_archival(session, dry_run=True)
        assert results["audit_log"] == 0
        assert results["ai_conversations"] == 0
