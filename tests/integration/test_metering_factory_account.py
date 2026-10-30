"""The metered provider's account must stay usable after `get_provider()` returns.

`get_provider()` loads the account in a short `get_session()` block. That block COMMITS on exit,
and the commit expires every loaded attribute — so the first `account.plan` read inside the
meter (`check_and_reserve`, `billing_period`) tried to refresh a detached instance and raised
`DetachedInstanceError`. The wrapper fails open by design, so every metered AI call since at least
2026-10-15 went through **uncounted and uncapped**, visible only as ERROR lines in evo-dev's log.
The unit tests never built a provider from a real account, which is how it survived.
"""

from __future__ import annotations

from mihomes.services.ai import provider as provider_mod
from mihomes.services.metering.ai_wrapper import MeteredProvider
from mihomes.tenancy.context import account_context


class _Fake:
    def complete(self, system_prompt, query, context_data=None, attachments=None):
        return "ok"


def test_factory_account_is_readable_after_the_session_closes(account_a, monkeypatch):
    monkeypatch.setattr(provider_mod, "_construct", lambda *a, **k: _Fake())
    with account_context(account_a):
        p = provider_mod.get_provider("nim", "k", entry_point="test.factory")
    assert isinstance(p, MeteredProvider)
    account = p._account
    # These are exactly the reads the meter makes; each raised DetachedInstanceError before.
    assert account.id == account_a
    assert account.plan
    assert hasattr(account, "current_period_end") and hasattr(account, "subscription_status")


def test_metered_call_is_counted(account_a, monkeypatch, caplog):
    """End to end: one metered call records usage and logs no metering error."""
    import logging

    from sqlalchemy import func, select

    from mihomes.db import get_session
    from mihomes.models.ai_usage import AIUsageEvent

    monkeypatch.setattr(provider_mod, "_construct", lambda *a, **k: _Fake())
    with account_context(account_a):
        with get_session() as s:
            before = s.execute(select(func.count()).select_from(AIUsageEvent)).scalar()
        p = provider_mod.get_provider("nim", "k", entry_point="test.factory")
        with caplog.at_level(logging.ERROR, logger="mihomes.services.metering.ai_wrapper"):
            assert p.complete("sys", "q") == "ok"
        with get_session() as s:
            after = s.execute(select(func.count()).select_from(AIUsageEvent)).scalar()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.text
    assert after == before + 1
