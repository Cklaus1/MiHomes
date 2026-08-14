"""M38 · `report upcoming` renders contract renewals without crashing.

`cli/report.py` referenced `c.vendor.name`, but `Vendor` exposes `company_name`
— so any contract-renewal row raised AttributeError, taking down the whole
`report upcoming` command whenever a contract was expiring in the window.

Isolation note: `mihomes.config.DB_URL` is frozen at import time, so setting
`MIHOMES_DIR` here has no effect once any sibling module has imported the
package. This fixture instead rebinds the *global* engine to an explicit
temp-file URL and restores the previous engine on teardown, leaving the
engines other integration modules depend on untouched (and never writing to
the developer's real ``~/.mihomes`` database).
"""

import os
import tempfile
from datetime import date, timedelta

# Freeze config paths to a throwaway dir BEFORE importing mihomes — matches the
# convention in test_cli.py / test_demo_boot.py. `config.DB_URL` is captured at
# import time, so a module that imports the package first (e.g. when this file
# collects before test_cli.py) must not let it point at the real ~/.mihomes DB.
os.environ.setdefault("MIHOMES_DIR", tempfile.mkdtemp())


# Imports are deliberately below the MIHOMES_DIR assignment: mihomes.config binds its
# paths at import time, so importing earlier would freeze the real home directory.
import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from mihomes import db  # noqa: E402
from mihomes.cli import app  # noqa: E402

runner = CliRunner()


@pytest.fixture
def report_db(tmp_path, monkeypatch):
    """Isolated, migrated DB bound to a fresh temp-file engine for this test."""
    db_path = tmp_path / "report.db"
    url = f"sqlite:///{db_path}"
    # `is_initialized()` (and the CLI's guard) read config.DB_PATH at call time;
    # point it (and DB_URL) at our temp file so the guard passes and every query
    # hits the same isolated DB. monkeypatch restores both on teardown.
    from mihomes import config

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DB_URL", url)
    prev_engine, prev_factory = db._engine, db._SessionLocal
    db._engine = None
    db._SessionLocal = None
    db.init_db(url=url)  # migrate temp file; binds the global engine to `url`
    try:
        with db.get_session() as session:
            from mihomes.services.contract import create_contract
            from mihomes.services.property import create_property
            from mihomes.services.vendor import create_vendor

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
    finally:
        db.dispose_engine()
        db._engine, db._SessionLocal = prev_engine, prev_factory


def test_upcoming_report_renders_contract_renewal(report_db):
    result = runner.invoke(app, ["report", "upcoming", "--days", "30"])
    assert result.exit_code == 0, result.output
    assert "Orkin Pest Control" in result.output
    assert "Contract Renewal" in result.output
