"""/billing's plan cards — each plan offers the right action for how the account pays.

**The bug.** The page always showed "Upgrade to Pro" and "Upgrade to Estate", so a Pro account was
offered its own plan, and a Pro or Estate account had no way back to Free. It also branched on
`stripe_customer_id`, which `start_checkout` sets before the user reaches Stripe — an abandoned
checkout left "Manage billing" and no way to try again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from mihomes.entitlements.limits import PLAN_LIMITS
from mihomes.services.billing.plans import plan_cards


def _account(plan="free", status=None, *, customer=None, subscription=None, trial_ends=None):
    return SimpleNamespace(plan=plan, subscription_status=status, stripe_customer_id=customer,
                           stripe_subscription_id=subscription, trial_ends_at=trial_ends)


def _by_key(account):
    return {c.key: c for c in plan_cards(account)}


def test_free_is_offered_both_upgrades():
    cards = _by_key(_account("free"))
    assert cards["free"].current and cards["free"].action is None
    assert (cards["pro"].action, cards["pro"].action_label) == ("checkout", "Upgrade to Pro")
    assert (cards["estate"].action, cards["estate"].action_label) == ("checkout", "Upgrade to Estate")


def test_pro_without_stripe_is_not_offered_pro_again():
    """**The reported bug**, for a seeded or trialing Pro account."""
    cards = _by_key(_account("pro", "active"))
    assert cards["pro"].current and cards["pro"].action is None
    assert cards["estate"].action == "checkout"
    assert cards["free"].action is None, "no subscription to cancel; free is not sellable (D4)"


def test_a_trial_says_it_returns_to_free():
    ends = datetime(2026, 10, 7, tzinfo=UTC)
    cards = _by_key(_account("pro", "trialing", trial_ends=ends))
    assert cards["pro"].current
    assert "07 Oct 2026" in cards["free"].note
    assert cards["estate"].action == "checkout"


@pytest.mark.parametrize("plan", ["pro", "estate"])
def test_a_paying_customer_changes_plan_only_through_the_portal(plan):
    """Never a checkout form: it would open a second subscription and bill twice."""
    cards = _by_key(_account(plan, "active", customer="cus_1", subscription="sub_1"))
    others = [c for c in cards.values() if not c.current]
    assert cards[plan].current
    assert all(c.action == "portal" for c in others), [(c.key, c.action) for c in others]
    assert cards["free"].action_label == "Cancel subscription"


def test_paying_estate_switches_down_to_pro():
    cards = _by_key(_account("estate", "active", customer="cus_1", subscription="sub_1"))
    assert cards["pro"].action_label == "Switch to Pro"


def test_an_abandoned_checkout_can_still_upgrade():
    """A customer id with no subscription is still a Free account that has not paid."""
    cards = _by_key(_account("free", customer="cus_abandoned"))
    assert cards["pro"].action == "checkout"
    assert cards["estate"].action == "checkout"


def test_a_canceled_subscription_shows_free_as_current():
    """`account.plan` may still say pro; the gates already treat it as Free."""
    cards = _by_key(_account("pro", "canceled", customer="cus_1"))
    assert cards["free"].current and not cards["pro"].current
    assert cards["pro"].action == "checkout"


def test_features_come_from_the_limits_table():
    cards = _by_key(_account("free"))
    estate = dict((label, ok) for ok, label in cards["estate"].features)
    free = dict((label, ok) for ok, label in cards["free"].features)

    assert "Unlimited homes (fair use)" in estate
    assert "1 home" in free
    assert f"{PLAN_LIMITS['pro']['max_homes']} homes" in [lbl for _, lbl in cards["pro"].features]
    assert free["Invite household staff"] is False
    assert estate["Predictive maintenance"] is True
    assert dict((lbl, ok) for ok, lbl in cards["pro"].features)["Predictive maintenance"] is False


def test_no_card_ever_checks_out_free():
    for account in (_account("free"), _account("pro", "active"), _account("estate", "active"),
                    _account("pro", "trialing", trial_ends=datetime.now(UTC) + timedelta(days=3))):
        assert all(not (c.key == "free" and c.action == "checkout") for c in plan_cards(account))
