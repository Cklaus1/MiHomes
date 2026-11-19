"""POST /waitlist and the confirm loop (SPEC-001 A10, A12, §6 Step 7).

Step 7's verification is the full loop against ConsoleProvider: submit → token in
console → GET confirm → confirmed_at set.

Two criteria here defend against failure modes that are easy to introduce and hard
to notice:

- **A10** — a dead mail provider must not roll back the signup. The user can ask
  for a resend; a lost row is unrecoverable.
- **A12** — the response must be byte-identical for a new and an existing address.
  Anything else turns the endpoint into an email-enumeration oracle (§7-N3).

Postgres-only: Phase 0 does not use SQLite (D3), so these skip without
TEST_DATABASE_URL. Per conventions §0, a skip here is a RED gate.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

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
    reason="TEST_DATABASE_URL unset — Phase 0 is Postgres-only (D3)",
)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """Bring the landing tree to head once for this module."""
    env = {**os.environ, "MIGRATION_DATABASE_URL": TEST_DATABASE_URL}
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS waitlist CASCADE"))
        conn.execute(text(f"DROP TABLE IF EXISTS {VERSION_TABLE} CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-n", "landing", "upgrade", "head"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"landing migration failed:\n{result.stderr}"
    yield
    engine.dispose()


@pytest.fixture
def clean_table():
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM waitlist"))
    yield engine
    engine.dispose()


@pytest.fixture
def client(monkeypatch, clean_table):
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("LANDING_BASE_URL", "https://mihomes.ai")

    from mihomes.landing import create_landing_app
    from mihomes.landing.db import reset_landing_engine

    reset_landing_engine()
    return TestClient(create_landing_app(), raise_server_exceptions=False)


def _row(engine, email: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT email, confirmed_at, confirm_send_count, source, "
                 "utm_campaign, name FROM waitlist WHERE email = :e"),
            {"e": email},
        ).one_or_none()


def test_signup_creates_a_row(client, clean_table):
    response = client.post("/waitlist", data={"email": "Alex@Example.com"})
    assert response.status_code == 200

    row = _row(clean_table, "alex@example.com")
    assert row is not None, "the address must be normalized and persisted"
    assert row.confirmed_at is None, "double opt-in: not confirmed until the link is clicked"


def test_full_confirm_loop(client, clean_table, capsys):
    """Step 7's own verification: submit → token in console → confirm → confirmed_at."""
    client.post("/waitlist", data={"email": "loop@example.com"})

    printed = capsys.readouterr().out
    match = re.search(r"/waitlist/confirm\?token=([A-Za-z0-9_\-]+)", printed)
    assert match, f"no confirm URL in the console output:\n{printed[:600]}"
    token = match.group(1)

    confirm = client.get(f"/waitlist/confirm?token={token}")
    assert confirm.status_code == 200

    row = _row(clean_table, "loop@example.com")
    assert row.confirmed_at is not None, "clicking the link must set confirmed_at"


def test_no_email_enumeration(client, clean_table):
    """A12 — the response is identical for a new and an existing address (§7-N3)."""
    first = client.post("/waitlist", data={"email": "dup@example.com"})
    second = client.post("/waitlist", data={"email": "dup@example.com"})

    assert first.status_code == second.status_code == 200
    assert first.text == second.text, (
        "a distinguishable response makes POST /waitlist an enumeration oracle"
    )

    with clean_table.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM waitlist WHERE email = 'dup@example.com'")
        ).scalar_one()
    assert count == 1, "an upsert, not a second row"


def test_confirmed_address_still_gets_the_same_response(client, clean_table, capsys):
    """N3 holds for an ALREADY-CONFIRMED address too — the sharpest case.

    signup() returns no token once confirmed, so a naive handler would branch and
    render something different. That difference is the oracle.
    """
    client.post("/waitlist", data={"email": "known@example.com"})
    printed = capsys.readouterr().out
    token = re.search(r"token=([A-Za-z0-9_\-]+)", printed).group(1)
    client.get(f"/waitlist/confirm?token={token}")

    fresh = client.post("/waitlist", data={"email": "brand-new@example.com"})
    already = client.post("/waitlist", data={"email": "known@example.com"})
    assert fresh.text == already.text


def test_signup_survives_email_failure(client, clean_table, monkeypatch):
    """A10 — a send failure must not roll back the signup.

    The user can request a resend; a lost signup is unrecoverable, and the Phase 0
    gate counts signups (GTM:293).
    """
    from mihomes.services.email.provider import EmailSendError

    def boom(*args, **kwargs):
        raise EmailSendError("provider down")

    import mihomes.services.email.console_provider as console_mod
    monkeypatch.setattr(console_mod.ConsoleProvider, "send", boom)

    response = client.post("/waitlist", data={"email": "survivor@example.com"})
    assert response.status_code == 200, "the caller must not see the provider failure"

    row = _row(clean_table, "survivor@example.com")
    assert row is not None, "A10: the signup row must survive a failed send"


def test_invalid_email_is_rejected_without_a_row(client, clean_table):
    """A malformed address should not create a row — but must not leak either."""
    response = client.post("/waitlist", data={"email": "not-an-email"})
    assert response.status_code in (200, 400)

    with clean_table.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one()
    assert count == 0


def test_optional_fields_and_utm_are_persisted(client, clean_table):
    client.post(
        "/waitlist",
        data={
            "email": "rich@example.com",
            "name": "Alex",
            "num_homes": "2-3",
            "has_staff": "yes",
            "utm_campaign": "launch",
        },
    )
    row = _row(clean_table, "rich@example.com")
    assert row.name == "Alex"
    assert row.utm_campaign == "launch"
    assert row.source == "form"


def test_confirm_with_a_bad_token_does_not_500(client, clean_table):
    """Mail scanners and link-rewriters hit this URL with mangled tokens."""
    for bad in ("", "garbage", "x" * 200):
        response = client.get(f"/waitlist/confirm?token={bad}")
        assert response.status_code == 200, f"token={bad!r} produced {response.status_code}"


def test_confirm_is_idempotent_over_http(client, clean_table, capsys):
    """A6 at the route level: users click twice, scanners pre-fetch."""
    client.post("/waitlist", data={"email": "twice@example.com"})
    token = re.search(r"token=([A-Za-z0-9_\-]+)", capsys.readouterr().out).group(1)

    first = client.get(f"/waitlist/confirm?token={token}")
    second = client.get(f"/waitlist/confirm?token={token}")
    assert first.status_code == second.status_code == 200
    assert first.text == second.text


def test_raw_token_is_not_persisted(client, clean_table, capsys):
    """N7 — hash only, never the raw token, in the DB or the logs."""
    client.post("/waitlist", data={"email": "hashonly@example.com"})
    token = re.search(r"token=([A-Za-z0-9_\-]+)", capsys.readouterr().out).group(1)

    with clean_table.connect() as conn:
        stored = conn.execute(
            text("SELECT confirm_token_hash FROM waitlist WHERE email='hashonly@example.com'")
        ).scalar_one()

    assert stored != token
    assert len(stored) == 64
