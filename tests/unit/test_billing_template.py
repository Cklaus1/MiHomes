"""billing.html across every account state — each keeps its action and its test hook.

Rendered against a stub base so it runs without a database; the route-level tests in
`test_billing_routes.py` / `test_upgrade_prompt.py` cover the same page over HTTP.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

from mihomes.services.billing.provider import PriceInfo

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "mihomes" / "web" / "templates"
PRICES = {
    ("pro", "monthly"): PriceInfo(1900, "usd"), ("pro", "annual"): PriceInfo(19000, "usd"),
    ("estate", "monthly"): PriceInfo(4900, "usd"), ("estate", "annual"): PriceInfo(49000, "usd"),
}
END = datetime(2026, 10, 24, tzinfo=UTC)


def _account(plan="free", status=None, *, sub=None, interval=None, trial_ends=None, cancelling=False):
    return SimpleNamespace(plan=plan, subscription_status=status, stripe_customer_id="cus_1" if sub else None,
                           stripe_subscription_id=sub, billing_interval=interval, trial_ends_at=trial_ends,
                           cancel_at_period_end=cancelling, current_period_end=END if sub else None)


def _render(account, prices=PRICES):
    from mihomes.web.routes import billing as route

    env = jinja2.Environment(loader=jinja2.ChoiceLoader([
        jinja2.DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        jinja2.FileSystemLoader(str(TEMPLATES)),
    ]), undefined=jinja2.StrictUndefined)
    import mihomes.services.billing.plans as plans_mod

    original = plans_mod.plan_prices
    plans_mod.plan_prices = lambda provider=None: prices
    try:
        ctx = route._page_context(account)
    finally:
        plans_mod.plan_prices = original
    ctx.update(error=None, notice=None)
    return env.get_template("billing.html").render(**ctx)


def _forms(html, action):
    return re.findall(rf'<form method="post" action="/billing/{action}">(.*?)</form>', html, re.S)


def test_free_sees_prices_one_switch_and_checkout_per_plan():
    html = _render(_account())
    assert html.count("data-billing-switch data-start") == 1
    assert "Save 17%" in html
    assert "$19" in html and "$15.83" in html and "$190 billed yearly · save $38" in html
    assert [re.search(r'value="(\w+)"', f).group(1) for f in _forms(html, "checkout")] == ["pro", "estate"]
    assert "Most popular" in html
    assert "Everything in Free, plus:" in html and "Everything in Pro, plus:" in html
    assert 'data-testid="plan-free"' in html and 'data-testid="downgrade-free"' not in html


def test_without_prices_the_page_still_sells():
    html = _render(_account(), prices={})
    assert "$19" not in html and "Save" not in html
    assert len(_forms(html, "checkout")) == 2


def test_trial_can_still_downgrade_and_says_when_it_ends():
    html = _render(_account("pro", "trialing", trial_ends=datetime(2026, 10, 7, tzinfo=UTC)))
    assert "Free trial" in html and "Trial ends 07 Oct 2026" in html
    assert 'data-testid="downgrade-free"' in html and "Downgrade to Free" in html


def test_hand_set_pro_is_offered_estate_not_pro():
    html = _render(_account("pro", "active"))
    assert "Upgrade to Pro" not in html and "Upgrade to Estate" in html
    assert 'data-testid="downgrade-free"' in html


def test_monthly_subscriber_sees_what_they_pay_and_the_switch_to_yearly():
    html = _render(_account("pro", "active", sub="sub_1", interval="monthly"))
    assert "$19 a month · Renews 24 Oct 2026" in html
    assert 'data-testid="offer-yearly"' in html and "save $38 a year" in html
    offer = [f for f in _forms(html, "change") if 'value="annual"' in f and 'value="pro"' in f]
    assert offer, "the yearly offer posts the same plan with the annual interval"
    assert "Upgrade to Pro" not in html
    assert "Cancel subscription" in html and 'data-testid="downgrade-free"' in html
    assert "Status:" not in html, "no raw status values"


def test_yearly_subscriber_starts_on_yearly_and_gets_no_offer():
    html = _render(_account("pro", "active", sub="sub_1", interval="annual"))
    assert "$190 a year" in html
    assert 'data-testid="offer-yearly"' not in html
    assert 'data-start="annual"' in html
    assert 'name="interval" value="annual" data-interval-input' in html


def test_cancelling_offers_undo_and_no_second_cancel():
    html = _render(_account("pro", "active", sub="sub_1", interval="monthly", cancelling=True))
    assert 'data-testid="cancel-pending"' in html and "Keep Pro" in html
    assert "Ends 24 Oct 2026" in html
    assert 'data-testid="offer-yearly"' not in html
    assert 'data-testid="downgrade-free"' not in html


def test_past_due_explains_and_offers_the_card_update():
    html = _render(_account("pro", "past_due", sub="sub_1", interval="monthly"))
    assert "Your last payment didn't go through" in html and "Payment failed" in html
    assert 'data-testid="offer-yearly"' not in html


def test_non_stripe_estate_can_move_down_to_pro():
    html = _render(_account("estate", "active"))
    assert 'data-testid="downgrade-pro"' in html and "Downgrade to Pro" in html


@pytest.mark.parametrize("status", [None, "active"], ids=["no-status", "active"])
def test_complimentary_estate_reads_as_deliberate_not_broken(status):
    """A hand-set Estate (the founder's own account): nothing is billed, so the page says so."""
    html = _render(_account("estate", status))
    estate = html[html.index('data-testid="plan-estate"'):]
    assert "Complimentary" in html and "Included at no charge · Not billed" in html
    assert "$0" in estate and "no charge" in estate
    assert "Normally $49 / month" in estate, "list price only as what starting would cost"
    assert estate.index("$0") < estate.index("Normally $49")

    pro = html[html.index('data-testid="plan-pro"'):html.index('data-testid="plan-estate"')]
    assert 'data-testid="downgrade-pro"' in pro, "the move down sits on the Pro card itself"
    assert html.count('data-testid="downgrade-pro"') == 1
    assert 'data-testid="downgrade-free"' in html

    assert "data-billing-switch data-start" in html
    assert [re.search(r'value="(\w+)"', f).group(1) for f in _forms(html, "checkout")] == ["estate"]
    assert "Start paying for Estate" in html and "Most popular" not in html


def test_a_trial_is_not_complimentary():
    from mihomes.services.billing.plans import is_complimentary

    assert not is_complimentary(_account("pro", "trialing", trial_ends=datetime(2026, 10, 7, tzinfo=UTC)))
    assert not is_complimentary(_account("pro", "active", sub="sub_1", interval="monthly"))
    assert not is_complimentary(_account())
    assert is_complimentary(_account("pro", None))


def test_estate_subscriber_switches_down_to_pro_on_the_confirm_screen():
    html = _render(_account("estate", "active", sub="sub_1", interval="annual"))
    assert "Switch to Pro" in html and "Upgrade to Pro" not in html
    assert [re.search(r'value="(\w+)"', f).group(1) for f in _forms(html, "change")] == ["pro"]
    assert not _forms(html, "checkout"), "a paying account never gets a second checkout"
