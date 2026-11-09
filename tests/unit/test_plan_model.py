"""Claude's model follows the plan (launch prices, 2026-11-09).

The AI allowances in `entitlements/limits.py` are sized to the model each plan buys, so a Pro
account silently answering on Sonnet would cost about 30x what its price was set against.
"""

import uuid
from types import SimpleNamespace

import pytest

from mihomes.services.ai import ai_config
from mihomes.services.ai.ai_config import DEFAULT_MODEL, PLAN_MODELS, get_ai_model, plan_model
from mihomes.tenancy.context import account_context

HAIKU = PLAN_MODELS["standard"]


class _Session:
    """Just enough session: `get` returns the one account, or raises."""

    def __init__(self, account=None, error=None):
        self._account, self._error = account, error

    def get(self, _model, _id):
        if self._error:
            raise self._error
        return self._account


def _account(plan, status=None):
    return SimpleNamespace(plan=plan, subscription_status=status)


@pytest.fixture(autouse=True)
def no_config(monkeypatch):
    monkeypatch.setattr(ai_config, "get_config", lambda session, key, default=None: default)


@pytest.mark.parametrize("plan,status,expected", [
    ("free", None, HAIKU),
    ("pro", "active", HAIKU),
    ("pro", "trialing", HAIKU),
    ("estate", "active", DEFAULT_MODEL),
    ("estate", "past_due", DEFAULT_MODEL),   # grace keeps the plan
    ("estate", "canceled", HAIKU),           # a canceled Estate account is on Free now
    ("bogus", None, HAIKU),
])
def test_plan_picks_the_model(plan, status, expected):
    with account_context(uuid.uuid4()):
        assert get_ai_model(_Session(_account(plan, status)), "claude") == expected


def test_no_bound_account_is_operator_or_system_and_gets_the_default():
    assert plan_model(_Session(_account("free"))) == DEFAULT_MODEL


@pytest.mark.parametrize("session", [_Session(None), _Session(error=RuntimeError("db down"))])
def test_unreadable_account_fails_to_the_cheaper_model(session):
    with account_context(uuid.uuid4()):
        assert plan_model(session) == HAIKU


def test_operator_override_wins(monkeypatch):
    monkeypatch.setattr(ai_config, "get_config",
                        lambda session, key, default=None: "claude-opus-5-5"
                        if key == "ai.claude_model" else default)
    with account_context(uuid.uuid4()):
        assert get_ai_model(_Session(_account("free")), "claude") == "claude-opus-5-5"


def test_other_providers_are_untouched():
    with account_context(uuid.uuid4()):
        assert get_ai_model(_Session(_account("estate", "active")), "nim") == "meta/muse-glimmer-30b"
