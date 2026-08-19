"""Tests for archive service."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from mihomes.models.ai_conversation import AIConversation
from mihomes.models.audit_log import AuditLog
from mihomes.services.archive import (
    ArchivalUnavailableError,
    _retention_cutoff,
    get_stats,
    run_archival,
)

# Placeholder ids for polymorphic entity_type/entity_id pairs. Distinct
# constants because several tests rely on two ids being DIFFERENT (filter by
# one, assert the other is excluded) — a single shared UUID would make those
# tests pass for the wrong reason. Were integers before SPEC-002 D2.
_ENTITY_1 = uuid.uuid4()


def _old_dt(years=3):
    """Return a datetime that is older than any default retention window."""
    return datetime.now(timezone.utc) - timedelta(days=years * 365)


def _recent_dt():
    return datetime.now(timezone.utc) - timedelta(days=10)


def _make_audit(session, timestamp=None):
    entry = AuditLog(
        entity_type="task", entity_id=_ENTITY_1, action="create",
        timestamp=timestamp or _old_dt(),
        # `actor` lost its `"admin"` default in SPEC-003 G3 (F6: the default recorded a principal
        # that had not acted). It is NOT NULL, so a direct construction must name someone; this
        # fixture is an unattended path, hence the same honest label `record_change` falls back to.
        actor="system",
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
        stats = get_stats(session)
        assert len(stats) == 2
        tables = {s["table"] for s in stats}
        assert "audit_log" in tables
        assert "ai_conversations" in tables

    def test_counts_eligible_rows(self, session):
        _make_audit(session, timestamp=_old_dt(years=3))  # old — eligible
        _make_audit(session, timestamp=_recent_dt())       # recent — not eligible
        stats = get_stats(session)
        audit_stats = next(s for s in stats if s["table"] == "audit_log")
        assert audit_stats["active_rows"] == 2
        assert audit_stats["eligible_to_archive"] == 1


class TestRunArchival:
    """Archival refuses while the archive tables are absent and untenanted (G10).

    **The previous tests in this class fabricated the tables they needed** —
    `CREATE TABLE IF NOT EXISTS audit_log_archive (id UUID, ...)` in a `_setup_archive_tables`
    helper — and then asserted that archival worked. No migration in the current tree creates
    those tables, so what those tests proved was that the code works against a schema invented
    by the test. Worse, the fabricated table had **no `account_id`**, so they asserted that
    tenant rows move into an untenanted table: the exact leak, encoded as an expectation.

    They are replaced rather than repaired. Preserved knowledge, so it is not lost with them:

    * **M8** — the raw DELETE must archive exactly the rows the ORM counted. The original bug
      interpolated the cutoff as a `T`-separated ISO literal while SQLite stored DateTime
      space-separated, and since `' ' < 'T'` a row *at* the cutoff was excluded by the ORM's
      strict `<` but included by the string comparison, archiving a row still inside its
      retention window. The fix was bound datetime parameters. Any replacement must keep the
      cutoff bound, not formatted.
    * The insert-then-delete order, and that `dry_run` must not delete.

    Both are recorded in `_run_archival_unreachable`'s docstring in the service, next to the
    code that implements them.
    """

    def test_run_archival_refuses(self, session):
        with pytest.raises(ArchivalUnavailableError) as exc:
            run_archival(session, dry_run=False)
        assert "not tenant-aware" in str(exc.value)

    def test_dry_run_also_refuses(self, session):
        """A dry run that reported success while the real run could not would be a worse lie."""
        with pytest.raises(ArchivalUnavailableError):
            run_archival(session, dry_run=True)

    def test_get_stats_reports_archival_unavailable(self, session):
        """`already_archived` is None, not 0.

        Reporting 0 would read as "nothing archived yet" rather than "cannot be answered", and
        the old code produced that 0 from `except Exception` around a failing query — which on
        Postgres also aborts the transaction and makes the *next* query fail somewhere else.
        """
        for row in get_stats(session):
            assert row["already_archived"] is None
            assert row["archival_available"] is False

    def test_the_archive_tables_really_are_absent(self, _pg_engine):
        """The premise this whole class rests on, asserted structurally.

        If someone adds tenant-aware archive tables, this fails and it is the signal to
        re-enable archival (and to re-derive the M8 coverage above) rather than to delete the
        assertion.

        **Deliberately a schema query, not a source grep.** The first version of this test
        searched test files for `CREATE TABLE ... audit_log_archive` and failed on the string
        inside this class's own docstring — the identical mistake logged in G6.3, where a guard
        asserted `"waitlist" not in baseline_source` and tripped over the baseline's own comment.
        Twice now, so the rule earns its place in `lessons.md`: **assert on structure, never on
        the text of source files.**
        """
        from sqlalchemy import inspect

        from mihomes.models import Base

        present = set(inspect(_pg_engine).get_table_names())
        archive_tables = {"audit_log_archive", "ai_conversations_archive"}
        assert not (archive_tables & present), (
            f"archive tables now exist ({sorted(archive_tables & present)}) — if they are "
            "tenant-aware (UUID keys, account_id, in TENANT_TABLES, RLS policy, drift-guard "
            "link), re-enable run_archival and restore the M8 boundary coverage"
        )
        assert not (archive_tables & set(Base.metadata.tables)), (
            "archive tables are on Base.metadata but no migration creates them — the state "
            "that made get_stats() fail with InFailedSqlTransaction"
        )
