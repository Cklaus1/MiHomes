"""G-CLI · C.3/M42 — dashboard must surface AI-panel failure reasons, not
swallow every error behind a misleading "not configured" message.

The AI recommendations panel wrapped ``dashboard_summary`` in a bare
``except Exception`` that replaced *any* failure — a real provider/network
error included — with a static "AI recommendations unavailable / configure"
line and logged nothing. This test drives a genuine provider error and asserts
the reason surfaces (and is logged).
"""

import os
import tempfile

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
def setup_db(cli_database):
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

    with account_context(account_id):
        with get_session() as session:
            from mihomes.models.property import Property
            from mihomes.services.demo import load_demo_data
            if not session.query(Property).filter(Property.slug == "beach-house").first():
                load_demo_data(session)
        yield


@pytest.fixture(autouse=True)
def _not_demo(monkeypatch):
    # The AI panel is skipped under MIHOMES_DEMO; ensure it runs.
    monkeypatch.delenv("MIHOMES_DEMO", raising=False)


def test_dashboard_surfaces_ai_error_reason(monkeypatch, caplog):
    from mihomes.services.ai.provider import AIProviderError

    def _boom(*a, **k):
        raise AIProviderError("rate limit exceeded (429)")

    # Patch where the CLI imports it (function-local import from orchestrator).
    monkeypatch.setattr(
        "mihomes.services.ai.orchestrator.dashboard_summary", _boom
    )

    with caplog.at_level("ERROR"):
        result = runner.invoke(app, ["dashboard"])

    # Command still renders (exit 0) — the AI panel failing must not crash it.
    assert result.exit_code == 0, result.output
    # The specific reason must surface to the user, not a generic "not configured".
    assert "rate limit" in result.output.lower() or "429" in result.output
    # And it must be logged, not silently swallowed.
    assert any("dashboard" in r.message.lower() or "rate limit" in r.getMessage().lower()
               for r in caplog.records), "AI panel failure was not logged"


def test_dashboard_renders_without_ai(monkeypatch):
    """A configured-but-unavailable AI path still renders the rest of the board."""
    result = runner.invoke(app, ["dashboard"])
    assert result.exit_code == 0
    assert "Dashboard" in result.output
