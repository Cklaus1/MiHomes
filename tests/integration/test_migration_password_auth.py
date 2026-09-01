"""G2 · SPEC-010 §6 Step 2 — migration 0017 (A4, A5).

Runs against its **own scratch database**, not `TEST_DATABASE_URL`, for the reason
`test_pg_baseline.py` gives: the suite's schema is built by `create_all`, so asserting a
migration there would test it against a schema something else created — which is how a
migration bug hides.

**A5 is the one worth reading carefully.** It asserts *two* things, and only the pair means
anything:

* two PASSWORD users cannot share a case-folded email;
* two GOOGLE users still can.

A table-wide `UNIQUE(email)` satisfies the first and fails the second — and it would break
Google identity, which `test_auth.py` separately defends. Asserting only the first half is
therefore worse than useless: it passes against the design error it is supposed to catch. That
is the harness's **G-partial** gate.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from tests.integration.test_pg_baseline import _config

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="needs a reachable Postgres; a skip here means Step 2 was NOT verified",
)


def _admin_url() -> str:
    return "postgresql+psycopg://postgres@localhost:5432/postgres"


@pytest.fixture
def scratch_db():
    """An empty database at `head`, dropped afterwards."""
    name = f"mihomes_pwauth_t{uuid.uuid4().hex[:10]}"
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = f"postgresql+psycopg://postgres@localhost:5432/{name}"
    try:
        yield url
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


def _cols(conn, table: str) -> dict[str, str]:
    return {
        r[0]: r[1]
        for r in conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t"
            ),
            {"t": table},
        )
    }


# ── A4 — the schema, up and down ──────────────────────────────────────────────

def test_up_down(scratch_db):
    """**A4** — a real Alembic up -> down -> up against a real database.

    Not `create_all`. The three `users` alterations and the new table have to survive an
    actual migration run, including the downgrade, or a deploy that rolls back leaves a schema
    no later migration can apply to.
    """
    cfg = _config(scratch_db)
    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db, future=True)
    try:
        with engine.connect() as c:
            users = _cols(c, "users")
            assert "password_hash" in users, "0017 did not add users.password_hash"
            assert "password_set_at" in users
            assert users["password_hash"] == "YES", "password_hash must be nullable"
            # D6 — the inversion this spec exists to make.
            assert users["google_sub"] == "YES", (
                "google_sub is still NOT NULL; a password user has no Google subject and "
                "sign-up would fail on the insert"
            )

            reset = _cols(c, "password_reset_tokens")
            assert reset, "0017 did not create password_reset_tokens"
            assert set(reset) == {
                "id", "user_id", "token_hash", "expires_at", "used_at", "created_at",
            }, f"unexpected columns: {sorted(reset)}"

            # GLOBAL: no RLS policy, unlike every tenant table (0016 is the contrast).
            policies = [
                r[0] for r in c.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = 'password_reset_tokens'")
                )
            ]
            assert policies == [], (
                f"password_reset_tokens has RLS policies {policies}. It is GLOBAL — a reset "
                "happens before sign-in, so a tenant policy returns zero rows and breaks it"
            )
    finally:
        engine.dispose()

    # Down, then up again. The second upgrade is what catches a downgrade that leaves
    # something behind — an index, a type, a column — that the next upgrade then collides with.
    command.downgrade(cfg, "0016_gateway_link_tokens")

    engine = create_engine(scratch_db, future=True)
    try:
        with engine.connect() as c:
            users = _cols(c, "users")
            assert "password_hash" not in users, "downgrade left password_hash behind"
            assert "password_set_at" not in users
            assert users["google_sub"] == "NO", "downgrade did not restore google_sub NOT NULL"
            assert _cols(c, "password_reset_tokens") == {}, "downgrade left the table behind"
            idx = [
                r[0] for r in c.execute(
                    text("SELECT indexname FROM pg_indexes WHERE indexname = 'uq_users_email_password'")
                )
            ]
            assert idx == [], "downgrade left the partial index behind"
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db, future=True)
    try:
        with engine.connect() as c:
            assert "password_hash" in _cols(c, "users"), "re-upgrade did not restore the column"
    finally:
        engine.dispose()


# ── A5 — G-partial: BOTH halves ───────────────────────────────────────────────

def test_partial_unique_index(scratch_db):
    """**A5 · G-partial** — the index is partial, proven from both sides.

    Half of this test passes against a table-wide `UNIQUE(email)`, which is exactly the design
    error it exists to catch. The Google half is the half that matters.
    """
    command.upgrade(_config(scratch_db), "head")
    engine = create_engine(scratch_db, future=True)

    def insert(conn, *, email, sub=None, pw=None):
        conn.execute(
            text(
                "INSERT INTO users (id, google_sub, email, password_hash) "
                "VALUES (:i, :s, :e, :p)"
            ),
            {"i": str(uuid.uuid4()), "s": sub, "e": email, "p": pw},
        )

    try:
        # --- half 1: two PASSWORD users cannot share a case-folded email -----
        with engine.begin() as c:
            insert(c, email="dup@example.com", pw="scrypt$32768$8$1$aaaa$bbbb")

        with engine.begin() as c:
            with pytest.raises(Exception) as exc:
                insert(c, email="dup@example.com", pw="scrypt$32768$8$1$cccc$dddd")
        assert "uq_users_email_password" in str(exc.value), (
            f"a second password user with the same email was accepted, or rejected by the "
            f"wrong constraint: {exc.value}"
        )

        # Case folding: DUP@ is the same login as dup@. Without `lower()` this inserts fine
        # and the login form then has two rows to choose between.
        with engine.begin() as c:
            with pytest.raises(Exception) as exc:
                insert(c, email="DUP@example.com", pw="scrypt$32768$8$1$eeee$ffff")
        assert "uq_users_email_password" in str(exc.value), (
            "the index is not case-folded — Alice@ and alice@ are two accounts"
        )

        # --- half 2: two GOOGLE users still CAN share one ---------------------
        # This is what proves the index is partial. A table-wide unique passes half 1 and
        # fails here, taking `test_auth.py`'s "same email, different sub = different people"
        # with it.
        with engine.begin() as c:
            insert(c, email="shared@example.com", sub="google-sub-1")
            insert(c, email="shared@example.com", sub="google-sub-2")

        with engine.connect() as c:
            n = c.execute(
                text("SELECT count(*) FROM users WHERE email = 'shared@example.com'")
            ).scalar()
        assert n == 2, (
            f"expected 2 Google users sharing an email, found {n}. The index is table-wide "
            "rather than partial, which breaks Google identity"
        )

        # --- and the boundary between them ------------------------------------
        # A Google user may share an address with a PASSWORD user too: the index covers only
        # rows where password_hash IS NOT NULL, so the Google row is invisible to it.
        with engine.begin() as c:
            insert(c, email="dup@example.com", sub="google-sub-3")

        with engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT count(*) FROM users WHERE lower(email) = 'dup@example.com'"
                )
            ).scalar()
        assert rows == 2, f"expected the password user plus one Google user, found {rows}"
    finally:
        engine.dispose()


def test_the_index_is_declared_on_the_model_too(scratch_db):
    """The drift guard's half of A5.

    `test_baseline_matches_metadata` autogenerates a diff between `Base.metadata` and the
    migrated schema. A raw-SQL index that exists in the database but not on the model is drift
    and fails that gate — so the model must declare it, and this asserts the declaration
    matches what the migration actually created rather than merely existing.
    """
    from mihomes.models.user import User

    declared = {i.name for i in User.__table__.indexes}
    assert "uq_users_email_password" in declared, (
        "the partial index is not declared on the User model. It exists only in 0017, so "
        "`test_baseline_matches_metadata` sees an unexplained index and fails"
    )

    command.upgrade(_config(scratch_db), "head")
    engine = create_engine(scratch_db, future=True)
    try:
        with engine.connect() as c:
            ddl = c.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_users_email_password'")
            ).scalar()
    finally:
        engine.dispose()

    assert ddl, "the index was not created by the migration"
    assert "UNIQUE" in ddl.upper()
    # Postgres renders the expression with an explicit cast — `lower((email)::text)` — so
    # match on `lower(` plus the column rather than a literal `lower(email`, which never
    # appears in `pg_indexes` however the index was written.
    normalised = ddl.lower().replace("(", "").replace(")", "").replace("::text", "")
    assert "loweremail" in normalised, f"not an expression index on lower(email): {ddl}"
    assert "password_hash IS NOT NULL" in ddl, (
        f"the index is not partial — this is the table-wide unique that breaks Google "
        f"identity: {ddl}"
    )
