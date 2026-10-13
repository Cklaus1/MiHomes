"""The plan page's prices, savings and "Everything in X, plus:" lists.

Prices come from the provider (Stripe), never a table in the code: the sandbox charged $19 while
the pricing doc said $20, and a copy in the page would have shown the wrong one.
"""

from __future__ import annotations

import pytest

from mihomes.services.billing import plans
from mihomes.services.billing.provider import PriceInfo

USD = {
    ("pro", "monthly"): PriceInfo(1900, "usd"),
    ("pro", "annual"): PriceInfo(19000, "usd"),
    ("estate", "monthly"): PriceInfo(4900, "usd"),
    ("estate", "annual"): PriceInfo(49000, "usd"),
}


class FlakyProvider:
    def __init__(self, fail: bool) -> None:
        self.fail, self.calls = fail, 0

    def get_price(self, *, plan, interval):
        self.calls += 1
        if self.fail:
            raise RuntimeError("stripe down")
        return USD[(plan, interval)]


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch):
    monkeypatch.setattr(plans, "_price_cache", {})


def test_money():
    assert plans.money(1900, "usd") == "$19"
    assert plans.money(1583, "usd") == "$15.83"
    assert plans.money(1234567, "usd") == "$12,345.67"
    assert plans.money(500, "chf") == "5 CHF"


def test_display_prices_show_yearly_as_per_month_with_the_computed_saving():
    p = plans._display_prices("pro", USD)
    assert p["monthly"] == {"amount": "$19", "per": "/ month", "note": "Billed monthly"}
    assert p["annual"]["amount"] == "$15.83"
    assert p["annual"]["note"] == "$190 billed yearly · save $38"


def test_yearly_saving_is_the_best_percentage_and_per_plan_amounts():
    s = plans.yearly_saving(USD)
    assert s["pct"] == 17
    assert s["pro"] == {"saving": "$38", "monthly": "$19", "annual": "$190"}


def test_no_saving_claimed_when_yearly_is_not_cheaper():
    flat = {("pro", "monthly"): PriceInfo(1000, "usd"), ("pro", "annual"): PriceInfo(12000, "usd")}
    assert plans.yearly_saving(flat) == {}
    assert "save" not in plans._display_prices("pro", flat)["annual"]["note"]


def test_prices_fail_soft_and_are_not_cached_on_failure():
    assert plans.plan_prices(FlakyProvider(fail=True)) == {}
    ok = FlakyProvider(fail=False)
    assert plans.plan_prices(ok) == USD and ok.calls == 4
    again = FlakyProvider(fail=True)
    assert plans.plan_prices(again) == USD and again.calls == 0, "cached after success"


def test_cards_without_prices_still_render_their_actions():
    from types import SimpleNamespace

    account = SimpleNamespace(plan="free", subscription_status=None, stripe_customer_id=None,
                              stripe_subscription_id=None, trial_ends_at=None,
                              cancel_at_period_end=False, current_period_end=None)
    cards = {c.key: c for c in plans.plan_cards(account, {})}
    assert cards["pro"].prices == {} and cards["pro"].action == "checkout"


def test_each_paid_plan_lists_only_what_it_adds():
    plus_of, added = plans._added_features("pro")
    assert plus_of == "Free"
    assert "Invite household staff" in added and "Vendor ratings" in added
    assert "Community support" not in added

    plus_of, added = plans._added_features("estate")
    assert plus_of == "Pro"
    assert "Predictive maintenance" in added and "Unlimited homes (fair use)" in added
    assert "Vendor ratings" not in added, "Pro already has it"

    assert plans._added_features("free")[0] == ""


def test_interval_comes_from_the_same_lookup_as_the_plan(monkeypatch):
    from mihomes.services.billing.prices import plan_and_interval_for_price_id, plan_for_price_id

    monkeypatch.setenv("STRIPE_PRICE_ESTATE_ANNUAL", "price_e_y")
    assert plan_and_interval_for_price_id("price_e_y") == ("estate", "annual")
    assert plan_for_price_id("price_e_y") == "estate"
    assert plan_and_interval_for_price_id("price_unknown") is None
