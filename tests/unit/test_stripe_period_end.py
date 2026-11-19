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


class _Retrieve:
    def __init__(self, obj):
        self.obj = obj

    def retrieve(self, _id):
        return self.obj


@pytest.mark.parametrize("pm,expected", [
    ({"type": "card", "card": {"brand": "visa", "last4": "4242"}}, "Visa ending 4242"),
    ({"type": "link", "link": {"email": "x@example.com"}}, "Paying with Link"),
    ({"type": "us_bank_account"}, "Saved payment method"),
])
def test_card_on_file_names_what_stripe_will_charge(provider, pm, expected):
    """The subscription's own payment method wins — that is where Checkout puts it."""
    provider._stripe = type("S", (), {
        "Subscription": _Retrieve({"default_payment_method": "pm_1"}),
        "Customer": _Retrieve({"invoice_settings": {}}),
        "PaymentMethod": _Retrieve(pm),
    })
    assert provider.get_card_on_file(customer_id="cus_1", subscription_id="sub_1") == expected


def test_card_on_file_falls_back_to_the_customer_default_and_to_none(provider):
    """No subscription (a card saved before buying): the customer's invoice default, where the
    add-card screen puts it. Nothing anywhere: None, which the page shows as no card."""
    card = {"type": "card", "card": {"brand": "mastercard", "last4": "4444"}}
    provider._stripe = type("S", (), {
        "Subscription": _Retrieve({}),
        "Customer": _Retrieve({"invoice_settings": {"default_payment_method": "pm_2"}}),
        "PaymentMethod": _Retrieve(card),
    })
    assert provider.get_card_on_file(customer_id="cus_1", subscription_id=None) == "Mastercard ending 4444"
    provider._stripe.Customer = _Retrieve({"invoice_settings": {}})
    assert provider.get_card_on_file(customer_id="cus_1", subscription_id=None) is None
