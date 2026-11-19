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
    downgrade_plan,
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


def test_estate_downgrades_to_pro_at_once(session, account):
    account.plan = "estate"
    session.commit()
    downgrade_plan(session, account, "pro")
    assert (account.plan, account.subscription_status) == ("pro", "active")


def test_downgrade_to_pro_refuses_the_wrong_cases(session, account):
    # Pro → Pro and Pro → Estate are not downgrades.
    with pytest.raises(NothingToCancel):
        downgrade_plan(session, account, "pro")
    with pytest.raises(NothingToCancel):
        downgrade_plan(session, account, "estate")
    # A paying Estate account switches in the Stripe portal, never by writing `plan` here.
    account.plan = "estate"
    session.commit()
    _pay(session, account)
    with pytest.raises(NothingToCancel):
        downgrade_plan(session, account, "pro")
    assert account.plan == "estate"


def test_pro_consequences_name_the_estate_features_that_go(session, account):
    account.plan = "estate"
    session.commit()
    c = downgrade_consequences(session, account, "pro")
    assert c["target"] == "pro" and c["at_period_end"] is False
    assert "Predictive maintenance" in c["lost_features"]
    assert "Vendor ratings" not in c["lost_features"], "Pro keeps vendor ratings"


def test_nothing_is_deleted(session, account):
    from mihomes.services.property import create_property

    for name in ("One", "Two"):
        create_property(session, name)
    session.commit()
    before = session.query(Property).count()

    cancel_plan(session, account, provider=RecordingProvider())
    assert session.query(Property).count() == before


# ── The cancellation email (2026-11-17) ──────────────────────────────────────────────────────


@pytest.fixture
def owned(session, account, monkeypatch):
    """`account` with an owner to email, on the console provider."""
    import uuid as _uuid

    from mihomes.ids import new_id
    from mihomes.models.membership import Membership
    from mihomes.models.user import User

    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    user = User(id=new_id(), google_sub=f"sub-cancel-{_uuid.uuid4().hex[:8]}",
                email="cancel-owner@example.com", email_verified_at=datetime.now(UTC))
    session.add(user)
    session.flush()
    session.add(Membership(id=new_id(), account_id=account.id, user_id=user.id,
                           role="owner", status="active"))
    session.commit()
    return account


def _cancel_mails(session):
    import json

    from mihomes.models.email_outbox import EmailOutbox

    rows = session.query(EmailOutbox).filter(EmailOutbox.template == "subscription_cancelled").all()
    return [(r.to_address, json.loads(r.context) if isinstance(r.context, str) else r.context)
            for r in rows]


def test_our_cancel_button_emails_once_even_after_stripe_confirms(session, owned):
    _pay(session, owned)
    cancel_plan(session, owned, provider=RecordingProvider())
    # Stripe's own confirmation of the same cancel must not send a second email.
    state = SubscriptionState("sub_1", "pro", "active", owned.current_period_end, True)
    apply_subscription_state(session, owned, state)

    mails = _cancel_mails(session)
    assert len(mails) == 1, mails
    to, ctx = mails[0]
    assert to == "cancel-owner@example.com"
    end = owned.current_period_end
    assert ctx["plan"] == "pro" and ctx["active_until"] == f"{end.day} {end:%B %Y}"
    assert ctx["billing_url"].endswith("/billing")


def test_a_cancel_made_in_the_stripe_portal_is_emailed(session, owned):
    _pay(session, owned)
    state = SubscriptionState("sub_1", "pro", "active", owned.current_period_end, True)
    apply_subscription_state(session, owned, state)
    apply_subscription_state(session, owned, state)  # Stripe redelivers: still one email
    assert len(_cancel_mails(session)) == 1


def test_no_email_when_the_period_simply_ends_or_for_a_trial(session, owned):
    # A trial (no subscription) is dropped to Free at once and is not "a cancelled subscription".
    cancel_plan(session, owned, provider=RecordingProvider())
    assert _cancel_mails(session) == []

    # The subscription ending at the period end is not a new cancel decision.
    owned.plan, owned.subscription_status = "pro", "active"
    session.commit()
    _pay(session, owned)
    ended = SubscriptionState("sub_1", "pro", "canceled", owned.current_period_end, False)
    apply_subscription_state(session, owned, ended)
    assert _cancel_mails(session) == []


def test_undo_then_cancel_again_emails_again(session, owned):
    _pay(session, owned)
    provider = RecordingProvider()
    cancel_plan(session, owned, provider=provider)
    resume_plan(session, owned, provider=provider)
    cancel_plan(session, owned, provider=provider)
    assert len(_cancel_mails(session)) == 2


def test_the_email_renders_from_what_is_queued(session, owned):
    from mihomes.services.email.render import render_template

    _pay(session, owned)
    cancel_plan(session, owned, provider=RecordingProvider())
    _, ctx = _cancel_mails(session)[0]
    subject, html, _ = render_template("subscription_cancelled", ctx)
    assert subject == "Your MiHomes subscription has been cancelled"
    assert f"You keep Pro until <strong>{ctx['active_until']}</strong>" in html
    assert "Resubscribe" in html
