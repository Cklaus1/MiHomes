"""G6 · §6 Step 6 — the Phase 4 migrations apply and revert cleanly (A30).

**Its own engine, running real Alembic**, because §9 states the gap plainly: `tests/conftest.py`
builds schema from `Base.metadata` and *"no existing test exercises Alembic"*. A test asserting
against `create_all` would prove nothing about the DDL that actually ships — including the RLS
carve-out, which `Base.metadata` does not describe at all.

**The revisions are enumerated from the versions directory, never named.** §4.4 describes one
migration creating five tables; it shipped as four (harness D1), because
`test_baseline_matches_metadata` fails the moment a model exists without a migration and this
phase's models land at Steps 2, 3, 4 and 6. A hand-listed round-trip would make A30's coverage
claim rot the moment `0016` lands — the same hand-list failure already fixed at F.3b, at
`cron setup`, and at A17's parametrization. Enumerating is strictly stronger than §4.4's single
revision: **each Phase 4 revision is round-tripped independently**, so a `downgrade` that only
works when its neighbours run first is caught here rather than at 3am on a rollback.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="needs a reachable Postgres; a skip here means A30 was NOT verified",
)

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "alembic" / "versions"

#: The first Phase 4 revision. Everything at or after it is this phase's work.
FIRST_PHASE4 = "0012"

#: What §4.4 says Phase 4 creates. Listed to catch a *deletion* — enumeration alone cannot:
#: a versions directory with one Phase 4 file would satisfy "every discovered revision
#: round-trips" perfectly while four tables had quietly stopped being created.
PHASE4_TABLES = {
    "email_suppressions",
    "email_deliveries",
    "email_outbox",
    "campaign_enrolments",
    "account_deletion_requests",
}

#: The one Phase 4 table with **no** RLS policy (D13/A21). Global because suppression belongs
#: to an address, not an account.
UNPOLICED = {"email_suppressions"}


def _admin_url() -> str:
    # `+psycopg` is required: a bare `postgresql://` resolves to psycopg2, not installed here.
    return "postgresql+psycopg://postgres@localhost:5432/postgres"


def _scratch_url(name: str) -> str:
    return f"postgresql+psycopg://postgres@localhost:5432/{name}"


@pytest.fixture
def scratch_db():
    """An empty database, dropped afterwards. `psql` is not on PATH here."""
    name = f"mihomes_phase4_t{uuid.uuid4().hex[:10]}"
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield _scratch_url(name)
    finally:
        with admin.connect() as c:
            c.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    # Suppress alembic/env.py's `fileConfig`: it defaults to `disable_existing_loggers=True`,
    # so every `command.upgrade()` here would switch off loggers configured earlier in the
    # session. Measured in `test_pg_baseline`, where it made three log-asserting tests fail in
    # the suite while passing alone.
    cfg.config_file_name = None
    return cfg


def phase4_revisions() -> list[str]:
    """Phase 4's revision ids, in order, discovered from the tree."""
    ids = []
    for path in sorted(VERSIONS.glob("*.py")):
        m = re.match(r"(\d{4})_", path.name)
        if m and m.group(1) >= FIRST_PHASE4:
            ids.append(path.stem)
    return ids


def test_the_enumeration_finds_every_phase4_table():
    """The enumeration must not silently return nothing, or everything below is vacuous.

    A `glob` matching no files would make each round-trip test pass by having nothing to do —
    the empty-set trap that makes a hand-written list dangerous, arriving through the back
    door. Asserted against the tables §4.4 names, so a *deleted* migration fails too.
    """
    revisions = phase4_revisions()
    assert len(revisions) >= 4, revisions
    assert revisions[0].startswith(FIRST_PHASE4)

    sources = "\n".join(
        (VERSIONS / f"{r}.py").read_text(encoding="utf-8") for r in revisions
    )
    for table in sorted(PHASE4_TABLES):
        assert f'"{table}"' in sources, f"no Phase 4 revision creates {table}"


def test_up_down(scratch_db):
    """**A30** — the Phase 4 migrations apply and revert cleanly.

    Up to head, then down past every Phase 4 revision one at a time, then up again. The
    second upgrade is what catches a `downgrade` that leaves an index, a policy or a trigger
    behind: the replay fails with "already exists" rather than passing quietly.
    """
    cfg = _config(scratch_db)
    revisions = phase4_revisions()

    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert PHASE4_TABLES <= tables, sorted(PHASE4_TABLES - tables)
    finally:
        engine.dispose()

    # Down one revision at a time, to the revision *before* Phase 4 began.
    command.downgrade(cfg, f"{revisions[0]}-1")

    engine = create_engine(scratch_db, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        left_behind = PHASE4_TABLES & tables
        assert not left_behind, f"downgrade left tables behind: {sorted(left_behind)}"
    finally:
        engine.dispose()

    # And up again — the replay that catches an incomplete downgrade.
    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert PHASE4_TABLES <= tables, sorted(PHASE4_TABLES - tables)
    finally:
        engine.dispose()


def test_the_enrolment_uniqueness_survives_the_migration(scratch_db):
    """One enrolment per (account, campaign) — the drip's idempotency guarantee (A25).

    `enrol()` is idempotent **because of this constraint**, not because it checks first: two
    concurrent enrolments both see "not present" and both insert, and the violation is the
    signal. Drop the constraint and every behavioural test stays green while the guarantee is
    gone and an account receives step 0 twice.

    Asserted against the live catalogue rather than the model, because A30 is about the DDL
    that actually ships — a `UniqueConstraint` present in `Base.metadata` and absent from the
    migration is exactly the drift this module exists to catch.
    """
    command.upgrade(_config(scratch_db), "head")

    engine = create_engine(scratch_db, future=True)
    try:
        uniques = inspect(engine).get_unique_constraints("campaign_enrolments")
    finally:
        engine.dispose()

    columns = {tuple(u["column_names"]) for u in uniques}
    assert ("account_id", "campaign") in columns, (
        f"campaign_enrolments must be unique on (account_id, campaign) — found {columns}"
    )


def test_rls_is_enabled_on_every_phase4_tenant_table(scratch_db):
    """The DDL `Base.metadata` cannot describe, which is why A30 needs real Alembic.

    Four of the five tables carry a tenant policy; `email_suppressions` deliberately carries
    none (A21). Asserted against the live catalogue after `upgrade head`, so a migration that
    forgot `policy_statements` fails here rather than in production, where the symptom is one
    tenant reading another's queued mail.
    """
    command.upgrade(_config(scratch_db), "head")

    engine = create_engine(scratch_db, future=True)
    try:
        with engine.connect() as conn:
            policed = {
                row[0]
                for row in conn.execute(
                    text("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname='public'")
                )
            }
    finally:
        engine.dispose()

    for table in sorted(PHASE4_TABLES - UNPOLICED):
        assert table in policed, f"{table} has no RLS policy after upgrade head"

    for table in sorted(UNPOLICED):
        assert table not in policed, (
            f"{table} must have NO policy (A21) — a tenant-scoped suppression list re-mails "
            f"a complainer the first time they appear under a second account"
        )
