"""G6.2/G6.3/G6.4 · §6 Step 6 — `0001_pg_baseline` is the whole schema.

Replaces the two autogenerate oracles deleted with the legacy SQLite tree
(`test_migration_reconciliation.py`, `test_money_migration.py`). Those compared a SQLite
schema against `Base.metadata` and had to be **skipped** from G2 onward, because SPEC-002
deliberately breaks that agreement — `account_id` on 40 tables, per-account uniqueness,
composite indexes, UUID PKs. This asserts the same property against the tree that actually
runs, on Postgres, so it is strictly stronger than what it replaces and carries no skip.

Each test runs the migration against its **own scratch database**, not `TEST_DATABASE_URL`.
The suite's database is built by `create_all` (see `tests/conftest.py`), and running
`upgrade` there would mean asserting the migration against a schema something else created
— which is how a migration bug hides.
"""

import os
import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from alembic import command
from mihomes.models import Base
from mihomes.tenancy.drift_guard import parent_links

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="needs a reachable Postgres; a skip here means Step 6 was NOT verified",
)

# Tables on Base.metadata that this tree deliberately does not create.
# `waitlist` is G6.4 (owned by alembic_landing/); `dummy` is a test-only model that
# registers itself on the shared Base.metadata when its module is imported.
NOT_IN_THIS_TREE = {"waitlist", "dummy"}


def _admin_url() -> str:
    # `+psycopg` is required: a bare `postgresql://` URL resolves to psycopg2, which is
    # not installed here (the project uses psycopg 3).
    return "postgresql+psycopg://postgres@localhost:5432/postgres"


def _scratch_url(name: str) -> str:
    return f"postgresql+psycopg://postgres@localhost:5432/{name}"


@pytest.fixture
def scratch_db():
    """An empty database, dropped afterwards. `psql` is not on PATH here."""
    name = f"mihomes_baseline_t{uuid.uuid4().hex[:10]}"
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
    # Suppress alembic/env.py's `fileConfig(config.config_file_name)`.
    #
    # `logging.config.fileConfig` defaults to `disable_existing_loggers=True`, so every
    # `command.upgrade()` here would silently switch off loggers configured earlier in the
    # session. That is not hypothetical: it made three passing `test_email_service` tests
    # fail in the full suite while passing in isolation, because they assert on log
    # records. Clearing `config_file_name` makes env.py skip the call — these tests want a
    # database URL from alembic.ini, not its logging setup.
    cfg.config_file_name = None
    return cfg


def _counts(url: str) -> dict:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as c:
            return {
                "tables": sorted(
                    r[0] for r in c.execute(text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    ))
                ),
                "triggers": sorted(
                    r[0] for r in c.execute(text(
                        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
                    ))
                ),
                "enums": sorted(
                    r[0] for r in c.execute(text(
                        "SELECT DISTINCT t.typname FROM pg_type t "
                        "JOIN pg_enum e ON e.enumtypid = t.oid"
                    ))
                ),
                "guard_fns": sorted(
                    r[0] for r in c.execute(text(
                        "SELECT proname FROM pg_proc WHERE proname LIKE 'mihomes%'"
                    ))
                ),
            }
    finally:
        engine.dispose()


def test_upgrade_then_downgrade_is_clean(scratch_db):
    """The spec's verify clause, plus the second upgrade that makes it meaningful.

    A downgrade that leaves the 22 enum *types* behind looks clean — every table is gone —
    and then the next upgrade dies on "type already exists". `op.drop_table` does not drop
    the type it created, so only re-upgrading proves the downgrade was complete.
    """
    cfg = _config(scratch_db)

    command.upgrade(cfg, "head")
    first = _counts(scratch_db)
    # 43 domain tables + alembic_version = 44 at the SPEC-002 baseline.
    # SPEC-003 adds `onboarding_state` (0004, A17) → 45. The count is pinned deliberately: it is
    # what makes an *accidental* table addition visible, so raising it is a decision recorded in
    # the same commit as the migration, never a silent adjustment to make a run go green.
    assert len(first["tables"]) == 45, first["tables"]
    assert len(first["enums"]) == 22
    assert first["guard_fns"] == ["mihomes_assert_account_matches_parent"]

    command.downgrade(cfg, "base")
    empty = _counts(scratch_db)
    assert empty["tables"] == ["alembic_version"], empty["tables"]
    assert empty["triggers"] == []
    assert empty["enums"] == [], f"downgrade left enum types behind: {empty['enums']}"
    assert empty["guard_fns"] == []

    command.upgrade(cfg, "head")
    assert _counts(scratch_db) == first, "upgrade is not idempotent after a downgrade"


def test_baseline_matches_metadata(scratch_db):
    """Empty autogenerate diff — the oracle the deleted SQLite tests used to carry.

    This is also what protects the baseline's `trigger_ddl_statements(Base.metadata)` call:
    if a model gains a table without this migration being regenerated, the diff is
    non-empty and this fails, rather than the guard trying to trigger a table the
    migration never creates.
    """
    command.upgrade(_config(scratch_db), "head")
    engine = create_engine(scratch_db, future=True)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": True,
                    "include_object": lambda obj, name, type_, reflected, compare_to: not (
                        type_ == "table" and name in NOT_IN_THIS_TREE
                    ),
                },
            )
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"baseline has drifted from Base.metadata:\n{diff}"


def test_waitlist_is_not_in_the_baseline(scratch_db):
    """G6.4. SPEC-001 D1/D3 give the landing app a standalone one-table database, so a
    `waitlist` here would mean the two trees had started to overlap."""
    command.upgrade(_config(scratch_db), "head")
    assert "waitlist" not in _counts(scratch_db)["tables"]


def test_drift_guard_triggers_created_by_the_migration(scratch_db):
    """The migration must install the guard, not only `create_all`.

    `tests/integration/test_drift_guard.py` proves the guard exists in the *suite's*
    database, which `create_all` builds. That would stay green even if the migration
    forgot the trigger entirely — this closes that gap.
    """
    command.upgrade(_config(scratch_db), "head")
    actual = set(_counts(scratch_db)["triggers"])
    expected = len(parent_links(Base.metadata))
    assert len(actual) == expected, (
        f"migration created {len(actual)} drift triggers, metadata declares {expected}"
    )


def test_single_head_and_no_legacy_revisions():
    """G6.3. One linear chain, and the archived 40 are off the search path.

    Asserts the *chain*, not a fixed count: `0002_rls` legitimately extends it, and a test
    pinned to "exactly one revision" would fail every time a real migration was added. What
    must stay true is that there is one head (no branching) and that the chain is short
    enough to be obviously just the SPEC-002 revisions rather than the archived 40.
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_config(_scratch_url("postgres")))
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single head, got {heads}"

    revs = [r.revision for r in script.walk_revisions()]
    assert "0001_pg_baseline" in revs, "the baseline is not on the path"
    assert revs[-1] == "0001_pg_baseline", (
        f"the baseline must be the root of the chain, but the root is {revs[-1]}"
    )
    # The 40 archived revisions all carry hex ids; the SPEC-002 chain is 000N_-prefixed.
    legacy = [r for r in revs if not r.startswith("000")]
    assert not legacy, f"legacy revisions are still on the search path: {legacy}"
