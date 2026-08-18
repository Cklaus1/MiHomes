"""G13.1 · §6 Step 13 — `--account <slug>` actually scopes ops commands.

**Gap found during G-Final's F.3a/F.3b reconciliation, not during G13 itself.** G13.1's own verify
clause names this exact scenario (`mihomes task list --account <slug>` returns only that account's
tasks), but no test anywhere exercised the explicit `--account` flag — every other CLI test runs
against an install with exactly one account, so `resolve_account()`'s **implicit** "the sole
account" path is the only one any test has ever taken. The **explicit** slug-resolution path
(`bootstrap.py`'s rule 1) had zero coverage. This is the actual tenant-selection mechanism for
every multi-account install, so it earns its own database rather than being folded into an
existing one — `cli_database` (conftest.py) deliberately never has a second account.
"""

from __future__ import annotations

import os
import tempfile

_test_dir = tempfile.mkdtemp()
os.environ["MIHOMES_DIR"] = _test_dir

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from mihomes.cli import app  # noqa: E402

runner = CliRunner()

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL unset — Postgres-only suite (conventions §0).",
)


@pytest.fixture(scope="module")
def two_account_db():
    """A private Postgres database with exactly TWO accounts, each with one property and task."""
    import mihomes.db as db_mod

    url = make_url(TEST_DATABASE_URL)
    two_db = f"{url.database}_two_acct"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        conn.exec_driver_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{two_db}' AND pid <> pg_backend_pid()"
        )
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{two_db}"')
        conn.exec_driver_sql(f'CREATE DATABASE "{two_db}"')

    two_url = str(url.set(database=two_db))
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = two_url
    db_mod.dispose_engine()

    from mihomes.db import get_session, init_db

    init_db()  # bootstraps ONE default account
    from mihomes.models.account import Account
    from mihomes.tenancy import account_context

    with get_session() as session:
        first = session.query(Account).one()
        first_slug = first.slug
        first_id = first.id
        second = Account(slug="second-household", name="Second Household",
                          type="household", plan="free")
        session.add(second)
        session.flush()
        second_id, second_slug = second.id, second.slug

    from mihomes.services.property import create_property
    from mihomes.services.task import create_task

    with account_context(first_id), get_session() as session:
        prop = create_property(session, "First House")
        create_task(session, "First-only task", prop.slug)

    with account_context(second_id), get_session() as session:
        prop = create_property(session, "Second House")
        create_task(session, "Second-only task", prop.slug)

    yield {"first_slug": first_slug, "second_slug": second_slug}

    db_mod.dispose_engine()
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    with admin.connect() as conn:
        conn.exec_driver_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{two_db}' AND pid <> pg_backend_pid()"
        )
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{two_db}"')
    admin.dispose()


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch):
    """`task list` renders 8 columns; at Rich's default non-terminal width (80) the Title column
    truncates task titles — see `test_report_upcoming.py` for the same class of fragility."""
    monkeypatch.setenv("COLUMNS", "200")


def test_no_account_flag_refuses_when_multiple_accounts_exist(two_account_db):
    """Rule 3 (bootstrap.py): the implicit "sole account" shortcut must stop the moment a second
    account exists, or an ambiguous command would silently write to whichever account happened
    to be created first."""
    result = runner.invoke(app, ["task", "list"])
    assert result.exit_code == 1
    assert "--account" in result.output


def test_account_flag_scopes_to_the_named_account(two_account_db):
    result = runner.invoke(app, ["--account", two_account_db["first_slug"], "task", "list"])
    assert result.exit_code == 0, result.output
    assert "First-only task" in result.output
    assert "Second-only task" not in result.output


def test_account_flag_scopes_to_the_other_account(two_account_db):
    """The same command, the other slug — proves the flag actually selects, not just accepts."""
    result = runner.invoke(app, ["--account", two_account_db["second_slug"], "task", "list"])
    assert result.exit_code == 0, result.output
    assert "Second-only task" in result.output
    assert "First-only task" not in result.output


def test_unknown_account_slug_is_refused(two_account_db):
    result = runner.invoke(app, ["--account", "no-such-account", "task", "list"])
    assert result.exit_code == 1
    assert "no-such-account" in result.output
