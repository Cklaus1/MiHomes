"""A3 — the landing migration applies and reverses cleanly on real Postgres.

Postgres-only by decision (SPEC-001 D3: "Phase 0 does not use SQLite"). Skips when
`TEST_DATABASE_URL` is unset, per the spec's §9 fixture note.

**A skip here is a RED gate, not a pass** (build-loop-conventions §0). This is the
only criterion that proves the database works at all, so the harness gates on this
node reporting `1 passed` rather than on the suite merely being green — a skipped
test still exits 0.
"""

import os
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

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

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


def test_the_main_alembic_tree_is_untouched():
    """The landing tree must not have disturbed `alembic/`.

    A harness non-negotiable: the single-user product keeps running on SQLite and
    its 40 revisions stay as they are. Patching them for Postgres is SPEC-002
    Step 6's job, not Phase 0's.
    """
    versions = REPO_ROOT / "alembic" / "versions"
    diff = subprocess.run(
        ["git", "diff", "--name-only", "origin/main", "--", str(versions)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert diff.stdout.strip() == "", (
        f"alembic/versions/ must be unchanged, but git reports:\n{diff.stdout}"
    )
