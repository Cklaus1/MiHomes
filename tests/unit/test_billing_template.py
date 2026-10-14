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


def _render(account, prices=PRICES, card=None):
    from mihomes.web.routes import billing as route

    env = jinja2.Environment(loader=jinja2.ChoiceLoader([
        jinja2.DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        jinja2.FileSystemLoader(str(TEMPLATES)),
    ]), undefined=jinja2.StrictUndefined)
    import mihomes.services.billing.plans as plans_mod

    original_prices, original_card = plans_mod.plan_prices, route.billing_service.card_on_file
    plans_mod.plan_prices = lambda provider=None: prices
    route.billing_service.card_on_file = lambda account, provider=None: card
    try:
        ctx = route._page_context(account)
    finally:
        plans_mod.plan_prices = original_prices
        route.billing_service.card_on_file = original_card
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
    free = html[html.index('data-testid="plan-free"'):html.index('data-testid="plan-pro"')]
    assert 'data-testid="downgrade-free"' in free and "Downgrade to Free" in free
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


def test_estate_subscriber_switches_down_to_pro_on_the_confirm_screen():
    html = _render(_account("estate", "active", sub="sub_1", interval="annual"))
    assert "Switch to Pro" in html and "Upgrade to Pro" not in html
    assert [re.search(r'value="(\w+)"', f).group(1) for f in _forms(html, "change")] == ["pro"]
    assert not _forms(html, "checkout"), "a paying account never gets a second checkout"


def _card(html, key):
    start = html.index(f'data-testid="plan-{key}"')
    nxt = [html.find(f'data-testid="plan-{k}"', start + 1) for k in ("free", "pro", "estate")]
    nxt = [n for n in nxt if n > start]
    return html[start:min(nxt)] if nxt else html[start:]


def test_every_owner_can_add_a_payment_method_without_buying_anything():
    for account in (_account(), _account("pro", "active", sub="sub_1", interval="monthly"),
                    _account("estate", "active")):
        html = _render(account)
        assert _forms(html, "payment-method"), "the button must not depend on the plan"
        assert "Add payment method" in html and "No card on file" in html


def test_a_saved_card_is_shown_and_the_button_offers_to_update_it():
    html = _render(_account("estate", "active", sub="sub_1", interval="monthly"), card="Visa ending 4242")
    assert "Visa ending 4242" in html and "No card on file" not in html
    assert "Update payment method" in html and "Add payment method" not in html


def test_moving_down_is_an_outlined_button_on_the_target_card():
    """Both downgrades are buttons on the card they lead to, outlined grey, never brand-filled."""
    html = _render(_account("estate", "active", sub="sub_1", interval="annual"))
    free = _card(html, "free")
    assert 'data-testid="downgrade-free"' in free and "Downgrade to Free" in free
    assert "border-gray-300" in free and "bg-brand-700" not in free

    html = _render(_account("estate", "active"))
    pro = _card(html, "pro")
    assert 'data-testid="downgrade-pro"' in pro and "border-gray-300" in pro
    assert html.count('data-testid="downgrade-pro"') == 1


def test_an_estate_subscriber_looks_like_any_other_paying_customer():
    """The founder's account, once on a real (sandbox) subscription: no special-casing."""
    html = _render(_account("estate", "active", sub="sub_1", interval="monthly"), card="Visa ending 4242")
    assert "Active" in html and "$49 a month · Renews 24 Oct 2026" in html
    assert "Invoices &amp; billing details" in html
    for absent in ("Complimentary", "Start paying", "Not billed", "Most popular"):
        assert absent not in html, absent


def test_most_popular_marks_an_upgrade_never_a_move_down():
    assert "Most popular" in _render(_account())
    assert "Most popular" not in _render(_account("estate", "active", sub="sub_1", interval="monthly"))
