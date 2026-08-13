"""Archive service — data retention and archival for high-volume tables.

**Archival is unavailable under multitenancy, and this module fails loudly rather than
pretending otherwise (SPEC-002 G10).**

`audit_log_archive` and `ai_conversations_archive` were created by a raw-SQL revision in the
SQLite chain and were never on `Base.metadata`. SPEC-002 Step 6 squashed that chain into
`0001_pg_baseline`, which does not create them — so **no migration in the current tree creates
these tables at all.** Measured: `run_archival()` raises
`UndefinedTable: relation "audit_log_archive" does not exist`.

To be precise about cause, because it invites the wrong fix: Step 6 *revealed* this, it did not
break it. `archive.py` depended on tables outside the managed metadata, and the squash — or any
fresh deploy — was always going to expose that. Reverting the squash would not make archival
correct.

Recreating them is not a small change and is deliberately **not** done here:

* their `id` columns are `INTEGER`, while G6.1 made every source `id` a UUIDv7 — so
  `INSERT INTO audit_log_archive (id, ...) SELECT id, ... FROM audit_log` cannot succeed even
  against the original schema;
* they have **no `account_id`**, so archived rows would have no tenant: not in the registry, no
  RLS policy, no drift-guard link. Archiving would silently move tenant data into an
  unprotected table.

A tenant-aware archive is retention's design decision (UUID keys, `account_id`, registry entry,
policy, drift guard), not something a raw-SQL audit step should invent. Until then
`run_archival()` refuses and `get_stats()` reports the archived count as unavailable instead of
fabricating `0`.

**Still true regardless, and G17.3's (A22) target:** `run_archival`'s `DELETE FROM audit_log`
is raw SQL, so the G8 ORM filter does not see it. It is defended by **RLS alone**, which means
on a superuser connection it would delete every tenant's rows.
"""

from datetime import datetime, timedelta, timezone

# Still needed by `_run_archival_unreachable`, which is kept as the record of retention
# semantics a tenant-aware replacement must preserve.
from sqlalchemy import text  # noqa: F401
from sqlalchemy.orm import Session

from mihomes.models.ai_conversation import AIConversation
from mihomes.models.audit_log import AuditLog
from mihomes.services.config_service import get_config


class ArchivalUnavailableError(RuntimeError):
    """Raised instead of letting `UndefinedTable` escape from three frames down.

    A caller can act on this: the message says what is missing and why, rather than naming a
    table the caller has never heard of.
    """


_ARCHIVE_UNAVAILABLE = (
    "Archival is unavailable: the archive tables (audit_log_archive, "
    "ai_conversations_archive) are not created by any migration in the current tree. They "
    "were defined by a raw-SQL revision in the SQLite chain that SPEC-002 Step 6 squashed, "
    "and they are not tenant-aware (integer primary keys, no account_id), so they cannot "
    "accept UUID-keyed rows or carry an account. A tenant-aware replacement is a retention "
    "design decision — see the module docstring in mihomes/services/archive.py."
)

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


_MODELS = {"audit_log": AuditLog, "ai_conversations": AIConversation}
_CUTOFF_COLUMN = {"audit_log": AuditLog.timestamp, "ai_conversations": AIConversation.created_at}


def get_stats(session: Session) -> list[dict]:
    """Return row counts for archivable tables — active and eligible, **per account**.

    **The active count is now ORM-based and therefore tenant-scoped.** It used to be
    `session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))`, which the G8 filter cannot
    see (a raw `text()` statement has no mappers), so it returned a **cross-tenant total** —
    one account's stats page reporting every account's row count. Counting through
    `session.query(Model)` picks up the tenant filter, which is what these numbers should
    always have meant. It also removes the table-name interpolation Step 10 is about; the names
    came from a hardcoded dict so it was not injectable, but it was still raw SQL where the ORM
    would do.

    `already_archived` is reported as `None` — "unavailable" — rather than `0`. The archive
    tables do not exist (see the module docstring), and the previous code hid that behind

        try:  ... except Exception: archived = 0

    which is worse than it looks **on Postgres**: the failed statement aborts the whole
    transaction, so the next unrelated query in the same session fails with
    `InFailedSqlTransaction: current transaction is aborted`. Measured. The broad `except` was
    written for SQLite, where a failed statement is local; carrying it to Postgres turned a
    missing table into a confusing error several frames away from its cause.
    """
    results = []

    for table_name, cfg in _ARCHIVABLE_TABLES.items():
        cutoff = _retention_cutoff(session, cfg["retention_key"], cfg["default_years"])
        years = get_config(session, cfg["retention_key"]) or str(cfg["default_years"])
        model = _MODELS[table_name]

        total = session.query(model).count()
        eligible = session.query(model).filter(_CUTOFF_COLUMN[table_name] < cutoff).count()

        results.append({
            "table": table_name,
            "description": cfg["description"],
            "active_rows": total,
            "eligible_to_archive": eligible,
            # None, not 0: the archive tables are absent, and reporting 0 would read as
            # "nothing has been archived yet" rather than "this cannot be answered".
            "already_archived": None,
            "archival_available": False,
            "retention_years": int(years),
            "cutoff_date": cutoff.date(),
        })

    return results


def run_archival(session: Session, dry_run: bool = False) -> dict:
    """Move rows older than the retention window into archive tables.

    **Currently refuses.** The archive tables do not exist in the current migration tree and
    are not tenant-aware; see the module docstring. Raising `ArchivalUnavailableError` here is
    the point — the alternative is `UndefinedTable` escaping from inside an `INSERT ... SELECT`,
    which tells the caller nothing about why.

    `dry_run` refuses too. A dry run that "succeeded" while the real run could not would be a
    worse lie than an error.
    """
    raise ArchivalUnavailableError(_ARCHIVE_UNAVAILABLE)


def _run_archival_unreachable(session: Session, dry_run: bool = False) -> dict:
    """The original implementation, kept for whoever builds tenant-aware archive tables.

    Deliberately unreachable rather than deleted: it records the retention semantics (the M8
    bound-datetime fix, the cutoff comparison, the insert-then-delete order) that a replacement
    has to preserve. **It cannot be re-enabled as-is** — it copies `id` into an INTEGER column
    and carries no `account_id` — and its `DELETE FROM audit_log` is raw SQL that the G8 filter
    does not see, so it is defended by RLS alone (A22 / G17.3).
    """
    results = {}

    # Audit log archival
    audit_cfg = _ARCHIVABLE_TABLES["audit_log"]
    cutoff = _retention_cutoff(session, audit_cfg["retention_key"], audit_cfg["default_years"])
    old_audit = session.query(AuditLog).filter(AuditLog.timestamp < cutoff).all()

    if old_audit and not dry_run:
        # M8: bind the cutoff as a datetime parameter rather than interpolating
        # its T-separated isoformat. SQLite stores DateTime space-separated, so a
        # lexical `timestamp < '...T...'` compare disagreed with the ORM count at
        # the cutoff boundary (' ' < 'T') and archived rows still within window.
        params = {"now": datetime.now(timezone.utc), "cutoff": cutoff}
        session.execute(text(
            "INSERT INTO audit_log_archive "
            "(id, timestamp, entity_type, entity_id, action, changes, actor, archived_at) "
            "SELECT id, timestamp, entity_type, entity_id, action, changes, actor, "
            ":now "
            "FROM audit_log WHERE timestamp < :cutoff"
        ), params)
        session.execute(text(
            "DELETE FROM audit_log WHERE timestamp < :cutoff"
        ), {"cutoff": cutoff})
    results["audit_log"] = len(old_audit)

    # AI conversations archival
    ai_cfg = _ARCHIVABLE_TABLES["ai_conversations"]
    cutoff = _retention_cutoff(session, ai_cfg["retention_key"], ai_cfg["default_years"])
    old_ai = session.query(AIConversation).filter(AIConversation.created_at < cutoff).all()

    if old_ai and not dry_run:
        # M8: bound datetime params (see audit_log branch above).
        params = {"now": datetime.now(timezone.utc), "cutoff": cutoff}
        session.execute(text(
            "INSERT INTO ai_conversations_archive "
            "(id, session_id, role, user_message, ai_response, context_summary, "
            "tokens_used, provider, model, created_at, updated_at, archived_at) "
            "SELECT id, session_id, role, user_message, ai_response, context_summary, "
            "tokens_used, provider, model, created_at, updated_at, "
            ":now "
            "FROM ai_conversations WHERE created_at < :cutoff"
        ), params)
        session.execute(text(
            "DELETE FROM ai_conversations WHERE created_at < :cutoff"
        ), {"cutoff": cutoff})
    results["ai_conversations"] = len(old_ai)

    return results
