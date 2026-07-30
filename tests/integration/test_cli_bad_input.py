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

import pytest
from typer.testing import CliRunner

from mihomes.cli import app
from mihomes.db import get_session, init_db

runner = CliRunner()

BAD = "not-a-date"


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    # MIHOMES_DIR / DB_URL freeze at first config import, so on-disk integration
    # modules share one DB file. Load demo idempotently so collection order can't
    # trip the "already loaded" guard.
    init_db()
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
