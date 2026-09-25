"""Renewal date from either Stripe subscription shape.

API 2025-03-31 moved `current_period_end` from the subscription onto each item. Reading only
the old field left "Renews" blank for every subscription made on the current API version.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mihomes.services.billing.stripe_provider import StripeProvider

END = 1_792_800_000


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_notreal")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_m")
    return StripeProvider()


def _sub(*, top=None, item=None):
    sub = {"id": "sub_1", "status": "active", "cancel_at_period_end": False,
           "items": {"data": [{"price": {"id": "price_pro_m"}}]}}
    if top is not None:
        sub["current_period_end"] = top
    if item is not None:
        sub["items"]["data"][0]["current_period_end"] = item
    return sub


@pytest.mark.parametrize("shape", [{"item": END}, {"top": END}], ids=["current-api", "legacy"])
def test_period_end_is_read_from_either_shape(provider, shape):
    state = provider._to_state(_sub(**shape))
    assert state.current_period_end == datetime.fromtimestamp(END, tz=UTC)
    assert state.plan == "pro"


def test_no_period_anywhere_stays_unknown(provider):
    assert provider._to_state(_sub()).current_period_end is None
