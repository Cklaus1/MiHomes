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

import pytest
from typer.testing import CliRunner

from mihomes.cli import app
from mihomes.db import get_session, init_db

runner = CliRunner()


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
