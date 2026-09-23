"""Downgrade to Free — at period end for a paid subscription, at once otherwise; nothing deleted."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mihomes.models.account import Account
from mihomes.models.property import Property
from mihomes.services.billing.cancel import (
    NothingToCancel,
    cancel_plan,
    downgrade_consequences,
    resume_plan,
)
from mihomes.services.billing.provider import SubscriptionState
from mihomes.services.billing.service import apply_subscription_state


class RecordingProvider:
    def __init__(self):
        self.cancelled: list[tuple[str, bool]] = []
        self.resumed: list[str] = []

    def cancel(self, *, subscription_id, at_period_end=True):
        self.cancelled.append((subscription_id, at_period_end))

    def resume(self, *, subscription_id):
        self.resumed.append(subscription_id)


@pytest.fixture
def account(session, account_a) -> Account:
    acct = session.get(Account, account_a)
    acct.plan = "pro"
    acct.subscription_status = "active"
    acct.stripe_customer_id = None
    acct.stripe_subscription_id = None
    acct.trial_ends_at = None
    acct.trial_used_at = None
    acct.cancel_at_period_end = False
    session.commit()
    return acct


def _pay(session, acct):
    acct.stripe_customer_id = "cus_1"
    acct.stripe_subscription_id = "sub_1"
    acct.current_period_end = datetime.now(UTC) + timedelta(days=12)
    session.commit()


def test_without_stripe_the_drop_is_immediate(session, account):
    provider = RecordingProvider()
    assert cancel_plan(session, account, provider=provider) == "now"
    assert (account.plan, account.subscription_status) == ("free", None)
    assert provider.cancelled == [], "no subscription, so nothing to tell Stripe"


def test_a_trial_keeps_trial_used_at(session, account):
    used = datetime(2026, 9, 1, tzinfo=UTC)
    account.subscription_status = "trialing"
    account.trial_ends_at = datetime.now(UTC) + timedelta(days=5)
    account.trial_used_at = used
    session.commit()

    cancel_plan(session, account, provider=RecordingProvider())
    assert account.plan == "free" and account.trial_ends_at is None
    assert account.trial_used_at == used, "cancelling a trial must not earn a second one"


def test_a_paid_plan_cancels_at_period_end(session, account):
    _pay(session, account)
    provider = RecordingProvider()

    assert cancel_plan(session, account, provider=provider) == "at_period_end"
    assert provider.cancelled == [("sub_1", True)]
    assert account.cancel_at_period_end is True
    assert account.plan == "pro" and account.subscription_status == "active", (
        "the customer keeps what they paid for until the period ends"
    )


def test_undo_keeps_the_subscription(session, account):
    _pay(session, account)
    provider = RecordingProvider()
    cancel_plan(session, account, provider=provider)

    resume_plan(session, account, provider=provider)
    assert provider.resumed == ["sub_1"]
    assert account.cancel_at_period_end is False


def test_cannot_cancel_twice_or_from_free(session, account):
    _pay(session, account)
    provider = RecordingProvider()
    cancel_plan(session, account, provider=provider)
    with pytest.raises(NothingToCancel):
        cancel_plan(session, account, provider=provider)
    assert len(provider.cancelled) == 1

    account.plan, account.subscription_status = "free", None
    account.stripe_subscription_id = None
    session.commit()
    with pytest.raises(NothingToCancel):
        cancel_plan(session, account, provider=provider)


def test_the_webhook_keeps_the_flag_in_step_with_stripe(session, account):
    """A cancel made in the Stripe portal shows on /billing too; an undo there clears it."""
    _pay(session, account)
    state = SubscriptionState("sub_1", "pro", "active", account.current_period_end, True)
    apply_subscription_state(session, account, state)
    assert account.cancel_at_period_end is True

    state = SubscriptionState("sub_1", "pro", "active", account.current_period_end, False)
    apply_subscription_state(session, account, state)
    assert account.cancel_at_period_end is False


def test_the_end_of_the_period_clears_the_pending_flag(session, account):
    """`customer.subscription.deleted` → canceled: nothing is pending any more."""
    _pay(session, account)
    cancel_plan(session, account, provider=RecordingProvider())
    ended = SubscriptionState("sub_1", "pro", "canceled", account.current_period_end, True)
    apply_subscription_state(session, account, ended)
    assert account.cancel_at_period_end is False
    with pytest.raises(NothingToCancel):
        resume_plan(session, account, provider=RecordingProvider())


def test_consequences_count_the_homes_that_freeze(session, account):
    from mihomes.services.property import create_property

    for name in ("One", "Two", "Three"):
        create_property(session, name)
    session.commit()

    c = downgrade_consequences(session, account)
    assert (c["homes"], c["frozen_homes"], c["at_period_end"]) == (
        session.query(Property).count(), session.query(Property).count() - 1, False,
    )


def test_nothing_is_deleted(session, account):
    from mihomes.services.property import create_property

    for name in ("One", "Two"):
        create_property(session, name)
    session.commit()
    before = session.query(Property).count()

    cancel_plan(session, account, provider=RecordingProvider())
    assert session.query(Property).count() == before
