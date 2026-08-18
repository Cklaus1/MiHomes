"""M38 · `report upcoming` renders contract renewals without crashing.

`cli/report.py` referenced `c.vendor.name`, but `Vendor` exposes `company_name`
— so any contract-renewal row raised AttributeError, taking down the whole
`report upcoming` command whenever a contract was expiring in the window.

**Migrated off its own private SQLite file to the shared `cli_database` (SPEC-002 §6 Step 15).**
`conftest.py`'s `cli_database` docstring already listed this module among the five sharing that
database — G15 never actually reconciled the file with that plan, and it kept its own throwaway
`sqlite:///...` engine plus a raw `db.init_db(url=...)` call, which `init_db()` now refuses
outright (`UnsupportedBackendError`, G6.2). Fixed to match `test_dashboard.py`'s established
pattern exactly: `cli_database` + `account_context` + a real `get_session()`.
"""

import os
import tempfile
from datetime import date, timedelta

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


@pytest.fixture(scope="module", autouse=True)
def report_db(cli_database):
    """Seed one contract due for renewal, once, in the account `cli_database` bootstraps.

    Module-scoped and idempotent (checks for the vendor before creating it) for the same reason
    as `test_dashboard.py`'s `setup_db`: several modules share one Postgres database and process,
    so collection order must not matter and a second collection pass must not re-insert.
    """
    from mihomes.db import get_engine
    from mihomes.tenancy.bootstrap import ensure_default_account

    init_db()
    account_id = ensure_default_account(get_engine())

    with account_context(account_id), get_session() as session:
        from mihomes.models.property import Property
        from mihomes.services.contract import create_contract
        from mihomes.services.property import create_property
        from mihomes.services.vendor import create_vendor

        if not session.query(Property).filter_by(slug="belle-estate").first():
            create_property(session, "Belle Estate")
            create_vendor(session, "Orkin Pest Control")
            create_contract(
                session,
                "orkin-pest-control",
                "belle-estate",
                start_date=date.today() - timedelta(days=300),
                end_date=date.today() + timedelta(days=10),
                service_category="pest_control",
            )
    yield


def test_upcoming_report_renders_contract_renewal(monkeypatch):
    """`COLUMNS=200`, not the CliRunner default.

    `report upcoming` shares one table across tasks/events/contracts/insurance, and by the time
    this runs `cli_database`'s demo data (long task titles, seeded by the other four modules
    sharing this database) is already loaded — so at Rich's default non-terminal width (80,
    `COLUMNS` unset) the Title column is squeezed and even a short vendor name like "Orkin Pest
    Control" wraps across rows. That happens to depend on collection order (this file runs last
    of the five, alphabetically, so demo data is always present by the time it does) — not
    flaky, just order-sensitive, and the real bug is the assertion assuming a wrap point rather
    than the fixed-but-inadequate default width. Rich re-reads `COLUMNS` per render, so setting
    it before the CLI call is enough; no `Console` needs rebuilding.
    """
    monkeypatch.setenv("COLUMNS", "200")
    result = runner.invoke(app, ["report", "upcoming", "--days", "30"])
    assert result.exit_code == 0, result.output
    assert "Orkin Pest Control" in result.output
    assert "Contract Renewal" in result.output
