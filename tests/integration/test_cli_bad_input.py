"""G-CLI · C.1/M39 — bad dates on CLI commands give a friendly error, not a raw
traceback.

Every command that parses a user-supplied date must route it through the shared
parser and raise ``typer.BadParameter`` (Click usage error, exit code 2) with an
"Invalid value" message — never leak a ValueError traceback. This covers the
sites the spec names: budget, report spending/vendors/compare, asset, task edit,
property occupy.
"""

import os
import tempfile

# Isolate the DB before importing the app.
_test_dir = tempfile.mkdtemp()
os.environ["MIHOMES_DIR"] = _test_dir


# Imports are deliberately below the MIHOMES_DIR assignment: mihomes.config binds its
# paths at import time, so importing earlier would freeze the real home directory.
import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from mihomes.cli import app  # noqa: E402
from mihomes.db import get_session, init_db  # noqa: E402
from mihomes.tenancy import account_context  # noqa: E402

runner = CliRunner()

BAD = "not-a-date"


@pytest.fixture(scope="module", autouse=True)
def setup_db(cli_database, upgrade_operator_account):
    """Initialize the CLI database and load demo data once per module.

    `cli_database` (root conftest) owns the dedicated Postgres database and sets `DATABASE_URL`,
    which `mihomes.db._active_url()` honours as of G13. The SQLite file this used to use no
    longer works at all: `0001_pg_baseline` is Postgres-native, so its `DEFAULT now()` on
    `created_at` makes the first INSERT into `accounts` fail with `unknown function: now()`.

    The `account_context` is required, not incidental — `load_demo_data` writes tenant-owned
    rows, and G8.3 stamps `account_id` from this ContextVar and raises `LookupError` without it.
    Demo data is loaded idempotently so collection order cannot trip the "already loaded" guard
    when several of these modules run in one session.
    """
    init_db()
    # init_db() runs the migrations AND bootstraps the account; this returns the existing one.
    from mihomes.db import get_engine
    from mihomes.tenancy.bootstrap import ensure_default_account
    account_id = ensure_default_account(get_engine())
    # SPEC-004 Step 8: the bootstrapped account is Free, and Free is now a real 1-home limit.
    # Demo seeding creates several properties, so raise the operator database's plan — see
    # `conftest.upgrade_operator_account` for why the fixture moves rather than the gate.
    upgrade_operator_account(account_id)

    with account_context(account_id):
        with get_session() as session:
            from mihomes.models.property import Property
            from mihomes.services.demo import load_demo_data
            if not session.query(Property).filter(Property.slug == "beach-house").first():
                load_demo_data(session)
        yield


def _assert_bad_param(result):
    """A bad date must exit non-zero without leaking a raw traceback."""
    assert result.exit_code != 0, f"expected non-zero exit, got 0; output={result.output!r}"
    # Only an orderly SystemExit is acceptable (typer.Exit / BadParameter);
    # a leaked ValueError means the parse ran outside a handler.
    if result.exception is not None:
        assert isinstance(result.exception, SystemExit), (
            f"leaked {type(result.exception).__name__}: {result.exception}"
        )
    assert "Traceback" not in result.output
    assert (
        "Invalid value" in result.output
        or "valid date" in result.output
        or "Invalid" in result.output
    ), f"no friendly error in output={result.output!r}"


def test_budget_set_bad_date():
    r = runner.invoke(
        app,
        ["budget", "set", "-p", "beach-house", "-c", "maintenance", "-a", "5000", "--start", BAD],
    )
    _assert_bad_param(r)


def test_expense_edit_bad_date():
    r = runner.invoke(app, ["expense", "edit", "1", "--date", BAD])
    _assert_bad_param(r)


def test_report_spending_bad_date():
    r = runner.invoke(app, ["report", "spending", "-p", "beach-house", "--start", BAD])
    _assert_bad_param(r)


def test_report_vendors_bad_date():
    r = runner.invoke(app, ["report", "vendors", "--start", BAD])
    _assert_bad_param(r)


def test_report_compare_bad_date():
    r = runner.invoke(app, ["report", "compare", "--start", BAD])
    _assert_bad_param(r)


def test_asset_edit_bad_date():
    r = runner.invoke(app, ["asset", "edit", "nonexistent-asset", "--warranty", BAD])
    _assert_bad_param(r)


def test_task_edit_bad_date():
    r = runner.invoke(app, ["task", "edit", "nonexistent-task", "--due", BAD])
    _assert_bad_param(r)


def test_property_occupy_bad_date():
    r = runner.invoke(app, ["property", "occupy", "beach-house", "--from", BAD])
    _assert_bad_param(r)
