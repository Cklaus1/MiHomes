"""R1.2 — permanent smoke net (spec §1.5).

Invoke *every* AI tool executor and *every* report section once against a
seeded (demo) database and assert none of them errors. This is the net that
would have caught D3, M44, and the enum-casing bugs on first run: those defects
manifested as `execute_tool` returning a "Tool error (...)" string (swallowed
exception) rather than a crash, so the suite stayed green while the tool was
silently broken. Here we treat that error string as a failure.

The AI provider is stubbed so the test needs no API key and exercises only the
deterministic DB-query / data-assembly paths — which is exactly where the
silent-corruption bugs lived.
"""

import pytest

from mihomes.services.ai import reports as reports_mod
from mihomes.services.ai import tools as tools_mod
from mihomes.services.ai.tools import _EXECUTORS, execute_tool
from mihomes.services.demo import load_demo_data


class _StubProvider:
    """Stands in for any AIProvider — returns fixed text, needs no network."""

    def __init__(self, *a, **k):
        pass

    def complete(self, system, user, attachments=None, **kwargs):
        return "STUB narrative."


@pytest.fixture
def seeded(session, monkeypatch):
    load_demo_data(session)
    session.flush()
    # Any get_provider() call (reports.py) yields the stub, and a fake key lets
    # the api-key lookup succeed without touching a real credential store.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(reports_mod, "get_provider", lambda *a, **k: _StubProvider())
    return session


# ── every tool executor, twice: unfiltered and property-scoped ────────────────

@pytest.mark.parametrize("tool_name", sorted(_EXECUTORS))
def test_every_tool_executor_runs_clean(seeded, tool_name):
    # unfiltered invocation
    out = execute_tool(seeded, tool_name, {})
    assert isinstance(out, str)
    assert not out.startswith("Tool error"), f"{tool_name} errored: {out}"
    assert not out.startswith("Unknown tool"), out

    # property-scoped invocation (demo seeds the 'beach-house' slug)
    out2 = execute_tool(seeded, tool_name, {"property_slug": "beach-house"})
    assert not out2.startswith("Tool error"), f"{tool_name} (scoped) errored: {out2}"


def test_no_executor_is_unreachable():
    """Guard: the registry is non-empty (catches an accidental wipe)."""
    assert len(_EXECUTORS) >= 15


# ── every report section ──────────────────────────────────────────────────────

def test_situation_report_runs_clean(seeded):
    resp = reports_mod.generate_situation_report(
        seeded, "The water heater is leaking in the basement.",
        subject="Leak", property_slug="beach-house",
    )
    assert resp is not None


def test_estate_digest_runs_clean(seeded):
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=30)
    resp = reports_mod.generate_estate_digest(seeded, start, end)
    assert resp is not None
