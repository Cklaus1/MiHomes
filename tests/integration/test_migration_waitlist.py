"""A3 — the landing migration applies and reverses cleanly on real Postgres.

Postgres-only by decision (SPEC-001 D3: "Phase 0 does not use SQLite"). Skips when
`TEST_DATABASE_URL` is unset, per the spec's §9 fixture note.

**A skip here is a RED gate, not a pass** (build-loop-conventions §0). This is the
only criterion that proves the database works at all, so the harness gates on this
node reporting `1 passed` rather than on the suite merely being green — a skipped
test still exits 0.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

# Imported rather than hardcoded: the landing tree names its version table
# explicitly so it can never contend with the main tree's, and a test repeating the
# literal would drift silently if that name changed.
from mihomes.migration_scope import VERSION_TABLE

REPO_ROOT = Path(__file__).resolve().parents[2]

# The landing app has its OWN database (SPEC-001 D1/D3: "shares the stack and
# nothing else", one table). SPEC-002's conftest also reads TEST_DATABASE_URL and
# runs create_all() over 44 tenant tables — pointed at one database, that breaks
# this module's "exactly {waitlist, alembic_version_landing}" assertion. Prefer a
# dedicated URL; fall back so a single-database setup still works.
TEST_DATABASE_URL = os.environ.get("LANDING_TEST_DATABASE_URL") or os.environ.get(
    "TEST_DATABASE_URL"
)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL unset — Phase 0 migrations are Postgres-only (D3)",
)

EXPECTED_COLUMNS = {
    "id", "email", "name", "num_homes", "has_staff", "source",
    "utm_campaign", "utm_source", "utm_medium", "referred_by",
    "confirm_token_hash", "confirm_sent_at", "confirmed_at",
    "confirm_send_count", "signup_ip", "user_agent",
    "created_at", "updated_at",
}


def _alembic(*args: str) -> subprocess.CompletedProcess:
    """Run the LANDING tree's alembic (`-n landing`), never the main one."""
    env = {**os.environ, "MIGRATION_DATABASE_URL": TEST_DATABASE_URL}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-n", "landing", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.fixture
def clean_db():
    """A schema with no waitlist table and no landing version row."""
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS waitlist CASCADE"))
        conn.execute(text(f"DROP TABLE IF EXISTS {VERSION_TABLE} CASCADE"))
        # The main tree's table too, in case a previous run pointed it here.
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    yield engine
    engine.dispose()


def test_upgrade_downgrade(clean_db):
    """A3 — upgrade → downgrade → upgrade, clean, on real Postgres.

    One revision, not 41: the landing tree is separate from `alembic/` precisely so
    it never replays the single-user product's SQLite-era history.
    """
    engine = clean_db

    up = _alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
    assert inspect(engine).has_table("waitlist")

    down = _alembic("downgrade", "base")
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"
    assert not inspect(engine).has_table("waitlist"), "downgrade must drop the table"

    # Re-upgrade: proves the downgrade left no residue that blocks a clean apply.
    again = _alembic("upgrade", "head")
    assert again.returncode == 0, f"re-upgrade failed:\n{again.stdout}\n{again.stderr}"
    assert inspect(engine).has_table("waitlist")


def test_landing_database_holds_only_the_waitlist_table(clean_db):
    """D3 — the landing database's only table is `waitlist`.

    The gate against the failure mode this tree exists to prevent: pointing
    target_metadata at Base.metadata (37 tables) instead of Waitlist.__table__
    would create the whole single-user schema here and still pass a naive
    "did the migration run?" check.
    """
    engine = clean_db
    assert _alembic("upgrade", "head").returncode == 0

    tables = set(inspect(engine).get_table_names())
    assert tables == {"waitlist", VERSION_TABLE}, (
        f"landing DB must hold exactly waitlist + {VERSION_TABLE}, got {sorted(tables)}"
    )


def test_schema_matches_the_model(clean_db):
    """Every column in §4.2 exists, with the types Postgres actually needs."""
    engine = clean_db
    assert _alembic("upgrade", "head").returncode == 0

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("waitlist")}
    assert set(cols) == EXPECTED_COLUMNS

    assert cols["id"]["type"].__class__.__name__ == "UUID"
    assert cols["email"]["nullable"] is False
    assert cols["has_staff"]["nullable"] is True, "three-state: yes/no/unanswered"
    assert cols["confirm_send_count"]["nullable"] is False

    # timestamptz, not naive — M7 is the existing tree's naive-DateTime hazard.
    for name in ("confirm_sent_at", "confirmed_at", "created_at", "updated_at"):
        assert cols[name]["type"].timezone is True, f"{name} must be tz-aware"

    index_names = {i["name"] for i in insp.get_indexes("waitlist")}
    assert "ix_waitlist_email" in index_names
    assert "ix_waitlist_confirm_token_hash" in index_names
    assert "ix_waitlist_confirmed_at" in index_names


def test_email_uniqueness_is_enforced_by_the_database(clean_db):
    """GTM:206 — one row per email. Enforced in the schema, not just the service."""
    import uuid

    engine = clean_db
    assert _alembic("upgrade", "head").returncode == 0

    from sqlalchemy.exc import IntegrityError

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO waitlist (id, email) VALUES (:i, :e)"),
            {"i": str(uuid.uuid4()), "e": "dup@example.com"},
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO waitlist (id, email) VALUES (:i, :e)"),
                {"i": str(uuid.uuid4()), "e": "dup@example.com"},
            )


def _created_tables(path: Path) -> set[str]:
    """Table names a revision passes to `op.create_table`.

    Parsed rather than substring-matched: the first version of this check asserted
    `"waitlist" not in baseline_source` and failed on the baseline's own **docstring**,
    which mentions `waitlist` to explain why it is absent. A test that a comment can
    break is testing the prose, not the schema.
    """
    return set(re.findall(r"op\.create_table\(\s*['\"]([A-Za-z_]+)['\"]", path.read_text(encoding="utf-8")))


def test_the_two_trees_do_not_overlap():
    """Neither tree may create the other's tables (G6.4).

    **This replaces a Phase 0 guard that SPEC-002 Step 6 inverted, exactly as that
    guard's own docstring predicted.** It used to require `alembic/versions/` to be
    byte-identical to `origin/main` — meaning "the landing work must not patch the
    product's 40 revisions; that is Step 6's job". Step 6 has now happened: those
    revisions moved to `alembic/legacy_sqlite/` and `0001_pg_baseline` replaced them, so
    the old assertion is false by design. A diff against `origin/main` cannot express the
    invariant anyway — `alembic_landing/` does not exist on that branch, so it reads as
    entirely changed no matter what.

    The invariant that actually matters is ownership, and it is checkable directly: the
    landing tree creates `waitlist` and nothing else, the product tree creates everything
    else and not `waitlist`. The failure this prevents is concrete — point both trees at
    one database and a shared table has two owners with two version rows fighting over it.
    """
    product = _created_tables(REPO_ROOT / "alembic" / "versions" / "0001_pg_baseline.py")
    assert product, "0001_pg_baseline creates no tables — did autogenerate run?"
    assert "waitlist" not in product, (
        "0001_pg_baseline creates `waitlist`, which alembic_landing/ owns"
    )

    landing_revisions = list((REPO_ROOT / "alembic_landing" / "versions").glob("*.py"))
    assert landing_revisions, "the landing tree has no revisions — did it move?"
    landing = set()
    for path in landing_revisions:
        landing |= _created_tables(path)
    assert landing == {"waitlist"}, (
        f"the landing tree must create only `waitlist`, but creates: {sorted(landing)}"
    )
    assert not (landing & product), f"both trees create: {sorted(landing & product)}"
