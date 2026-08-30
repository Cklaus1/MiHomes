"""G1.1 · §6 Step 1 — the link-token migration applies and reverts cleanly (A3).

**Its own engine, running real Alembic**, for the reason `test_migration_phase4.py` states at
length: `tests/conftest.py` builds schema from `Base.metadata`, so a test asserting against
`create_all` proves nothing about the DDL that actually ships — including the RLS policy and
the drift-guard trigger, which `Base.metadata` does not describe at all.

**The revision is discovered from the tree, not named.** Same reasoning as A30's enumeration:
a hand-named revision id rots the moment the file is renamed, and the rename is exactly when a
round-trip assertion stops running while still reporting green.

The `down` half is the half that matters. A `downgrade` is written once and run under pressure,
usually at the worst possible moment, and one that leaves an index or a policy behind fails on
the *replay* rather than on the rollback — so this goes up, down, and up again.
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
    reason="needs a reachable Postgres; a skip here means A3 was NOT verified",
)

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "alembic" / "versions"

TABLE = "gateway_link_tokens"

#: SPEC-006 §4.1 — *"the only new table"*. Named so a **deletion** fails too: discovery alone
#: cannot catch it, since a tree with no gateway revision would satisfy "the discovered
#: revision round-trips" perfectly while the table had quietly stopped being created.
EXPECTED_COLUMNS = {
    "id",
    "account_id",
    "membership_id",
    "gateway",
    "token_hash",
    "expires_at",
    "redeemed_at",
    "redeemed_by_sender",
}


def _admin_url() -> str:
    # `+psycopg` is required: a bare `postgresql://` resolves to psycopg2, not installed here.
    return "postgresql+psycopg://postgres@localhost:5432/postgres"


def _scratch_url(name: str) -> str:
    return f"postgresql+psycopg://postgres@localhost:5432/{name}"


@pytest.fixture
def scratch_db():
    """An empty database, dropped afterwards. `psql` is not on PATH here."""
    name = f"mihomes_gwlink_t{uuid.uuid4().hex[:10]}"
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
    # Suppress alembic/env.py's `fileConfig` — it defaults to `disable_existing_loggers=True`,
    # so each `command.upgrade()` would switch off loggers configured earlier in the session.
    cfg.config_file_name = None
    return cfg


def gateway_revision() -> str:
    """The revision that creates the link-token table, discovered by reading the tree."""
    for path in sorted(VERSIONS.glob("*.py")):
        if re.match(r"\d{4}_", path.name) and f'"{TABLE}"' in path.read_text(
            encoding="utf-8"
        ):
            return path.stem
    raise AssertionError(f"no alembic revision creates {TABLE}")


def test_the_discovery_finds_the_revision():
    """Guard on the guard: discovery must not return nothing.

    Every assertion below is driven by `gateway_revision()`. If the glob matched no file, the
    round-trip would pass by having nothing to do — the empty-set trap, arriving through the
    back door that a hand-written list is supposed to avoid.
    """
    revision = gateway_revision()
    assert revision.startswith("0016"), revision

    source = (VERSIONS / f"{revision}.py").read_text(encoding="utf-8")
    # The FK is deviation 2 in the migration's docstring: §4.2 omits it, and without it A10's
    # cascade is application code holding a promise the schema is supposed to keep.
    assert "memberships.id" in source, (
        "the migration must create the membership FK — without ondelete=CASCADE, A10 "
        "('revoking a membership removes its gateway link with no extra code') is not structural"
    )
    assert "CASCADE" in source


def test_up_down(scratch_db):
    """**A3** — the link-token migration applies and reverts cleanly.

    Up to head, down past the gateway revision, then up again. The second upgrade is what
    catches a `downgrade` that leaves an index, a policy or a trigger behind: the replay fails
    with "already exists" rather than passing quietly.
    """
    cfg = _config(scratch_db)
    revision = gateway_revision()

    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db, future=True)
    try:
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns(TABLE)}
        assert EXPECTED_COLUMNS <= columns, sorted(EXPECTED_COLUMNS - columns)
    finally:
        engine.dispose()

    command.downgrade(cfg, f"{revision}-1")

    engine = create_engine(scratch_db, future=True)
    try:
        assert TABLE not in inspect(engine).get_table_names(), (
            "downgrade left the table behind"
        )
    finally:
        engine.dispose()

    # And up again — the replay that catches an incomplete downgrade.
    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db, future=True)
    try:
        assert TABLE in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_rls_is_enabled_on_the_shipped_table(scratch_db):
    """§4.2 ships RLS with the table, and `Base.metadata` cannot express that.

    This is the assertion `create_all` can never make, and the reason A3 runs real Alembic:
    the ORM builds an identical-looking table with no policy at all, so a migration that
    forgot the policy would leave every behavioural test green over an unprotected table.
    """
    command.upgrade(_config(scratch_db), "head")

    engine = create_engine(scratch_db, future=True)
    try:
        with engine.connect() as conn:
            enabled = conn.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"),
                {"t": TABLE},
            ).scalar()
            assert enabled is True, f"row-level security is not enabled on {TABLE}"

            policies = conn.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = :t"),
                {"t": TABLE},
            ).scalars().all()
            assert policies, f"{TABLE} has RLS enabled but no policy — it denies everything"
    finally:
        engine.dispose()


def test_the_token_hash_unique_survives_the_migration(scratch_db):
    """Single-use redemption rests on this constraint, not on a check-then-insert (A9).

    Two concurrent redemptions both read "not yet redeemed" and both proceed; the violation is
    the signal. Drop the unique and every behavioural test stays green while a forwarded code
    can bind twice.

    It is on `token_hash` **alone**, deliberately: redemption looks a token up before any
    account is known, so a composite `(account_id, token_hash)` would leave the only query this
    table exists to serve unindexed — and would let two accounts mint the same hash.
    """
    command.upgrade(_config(scratch_db), "head")

    engine = create_engine(scratch_db, future=True)
    try:
        uniques = inspect(engine).get_unique_constraints(TABLE)
        by_name = {u["name"]: u["column_names"] for u in uniques}
        assert "uq_gateway_link_token_hash" in by_name, sorted(by_name)
        assert by_name["uq_gateway_link_token_hash"] == ["token_hash"], (
            "the unique must be on token_hash alone — redemption resolves a token before it "
            "knows which account it belongs to (§4.2's carve-out)"
        )
    finally:
        engine.dispose()
