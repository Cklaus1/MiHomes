"""G13 · §6 Step 13 — the trial state machine (A17, A18, A19).

**A17's phrasing is the whole test: "a trial grants Pro entitlements with *no Stripe subscription
existing*."** The absence is the assertion. A trial that quietly created a Stripe Customer or
subscription would grant the right entitlements and pass any test that only checked `can()` — while
creating vendor records for people who never convert, and making `start_checkout` reuse a customer
built before anyone agreed to pay (D4: Stripe objects at conversion only).

A18 and A19 are each about a *quiet* failure too: unlimited trials, and an expiry that silently
drops a home. Neither would produce an error anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mihomes.entitlements.limits import PLAN_LIMITS
from mihomes.entitlements.service import Allowed, Denied, can
from mihomes.models.account import Account
from mihomes.models.property import Property
from mihomes.services.billing.trial import (
    TRIAL_DAYS,
    TRIAL_PLAN,
    is_on_trial,
    maybe_start_trial,
    start_trial,
)


@pytest.fixture
def free_account(session, account_a) -> Account:
    account = session.get(Account, account_a)
    account.plan = "free"
    account.subscription_status = None
    account.trial_ends_at = None
    account.trial_used_at = None
    account.stripe_customer_id = None
    account.stripe_subscription_id = None
    session.commit()
    return account


class TestCardlessTrial:
    def test_cardless_trial_entitlements(self, session, free_account):
        """**A17** — Pro entitlements with **no Stripe subscription existing**.

        Both halves asserted. The entitlements half proves the trial works; the absence half
        proves it works the way §4.2 requires — app-managed state, no vendor object — and that is
        the half a "does the trial grant Pro" test would miss entirely.
        """
        assert isinstance(can(free_account, "property.add", {"current_homes": 1}), Denied)

        assert start_trial(session, free_account) is True

        assert free_account.plan == TRIAL_PLAN
        assert free_account.subscription_status == "trialing"
        assert isinstance(
            can(free_account, "property.add", {"current_homes": 1}), Allowed
        ), "a trial must grant real Pro limits through the ordinary entitlements path"

        assert free_account.stripe_customer_id is None, (
            "D4/§4.2 — no Stripe object during a card-less trial; creating one here would make "
            "start_checkout reuse a customer built before anyone agreed to pay"
        )
        assert free_account.stripe_subscription_id is None

    def test_the_trial_needs_no_special_case_in_can(self, session, free_account):
        """`trialing` already maps to *"the account's own plan"* in `_STATUS_TO_EFFECTIVE_PLAN`.

        Asserted because the tempting implementation is a trial-shaped branch inside `can()` — a
        second mechanism answering a question the first one already answers, and one that would
        drift the first time either changed.
        """
        start_trial(session, free_account)

        from mihomes.entitlements.limits import limits_for

        assert limits_for("pro", "trialing") == PLAN_LIMITS["pro"]

    def test_the_trial_is_fourteen_days(self, session, free_account):
        """§4.2's length, and `trial_ends_at` is what the sweep reads."""
        before = datetime.now(UTC)
        start_trial(session, free_account)

        # A tolerance window, not an equality: `start_trial` takes its own `now`, microseconds
        # after this one, so the end date is 14 days from *its* clock. Asserting `<= 14 days`
        # exactly failed by 2 milliseconds — a test flaky by construction, not a real defect.
        elapsed = free_account.trial_ends_at - before
        assert timedelta(days=TRIAL_DAYS) - timedelta(minutes=1) < elapsed
        assert elapsed < timedelta(days=TRIAL_DAYS) + timedelta(minutes=1)

    def test_is_on_trial_checks_the_date_not_just_the_status(self, session, free_account):
        """**The window the sweep cannot close.**

        A trial that ended at 03:00 is over at 03:00, not when the nightly job happens to run.
        Checking `subscription_status == "trialing"` alone would keep granting Pro for up to a day
        after expiry — so the date is checked here, and the sweep is what *tidies up* rather than
        what enforces.
        """
        start_trial(session, free_account)
        assert is_on_trial(free_account) is True

        free_account.trial_ends_at = datetime.now(UTC) - timedelta(minutes=1)
        assert is_on_trial(free_account) is False, (
            "an expired trial must stop granting immediately, not at the next sweep"
        )


class TestOneTrialEver:
    def test_one_trial_ever(self, session, free_account):
        """**A18** — a second trial on the same account is refused."""
        assert start_trial(session, free_account) is True
        assert start_trial(session, free_account) is False

    def test_refused_on_trial_used_at_not_on_plan(self, session, free_account):
        """**The case a plan check would get wrong.**

        Trial → convert to Pro → cancel back to Free. By then the account looks exactly like a new
        Free account, so a plan-based check hands them a second trial. `trial_used_at` is a record
        of history and survives every one of those transitions.
        """
        start_trial(session, free_account)

        # Converted, then cancelled: back to Free, no active subscription.
        free_account.plan = "free"
        free_account.subscription_status = "canceled"
        free_account.trial_ends_at = None
        session.commit()

        assert start_trial(session, free_account) is False, (
            "trial_used_at must outlive the plan — a cancelled ex-customer is not a new account"
        )

    def test_a_double_click_does_not_extend_the_trial(self, session, free_account):
        """Idempotent for a trial already running: `False`, not another fortnight."""
        start_trial(session, free_account)
        first_end = free_account.trial_ends_at

        assert start_trial(session, free_account) is False
        assert free_account.trial_ends_at == first_end

    def test_a_paying_customer_is_never_offered_a_trial(self, session, free_account):
        """An Estate customer at their seat cap hit a **real** limit on a plan they bought.

        Starting a trial there would set `plan="pro"` — a downgrade dressed as a gift, and one
        that would take effect silently.
        """
        free_account.plan = "estate"
        free_account.subscription_status = "active"
        free_account.stripe_subscription_id = "sub_paying"
        session.commit()

        assert maybe_start_trial(session, free_account, action="seat.add") is False
        assert free_account.plan == "estate"


class TestStartedOnFirstGatedAction:
    def test_the_gate_starts_the_trial(self, session, free_account):
        """§4.2 — the clock starts when the user wants the thing, not at signup.

        Exercised through the real gate: a Free account creating its second home is denied, the
        trial starts, and the action then succeeds. That end-to-end shape is the criterion —
        testing `maybe_start_trial` alone would prove the function works and not that anything
        calls it.
        """
        from mihomes.services.property import create_property

        create_property(session, "First Home")
        assert free_account.trial_used_at is None

        create_property(session, "Second Home")

        session.refresh(free_account)
        assert free_account.trial_used_at is not None, (
            "the second home is the gated action §4.2 starts the trial on"
        )
        assert free_account.plan == TRIAL_PLAN
        assert session.query(Property).count() == 2

    def test_the_denial_still_stands_when_no_trial_is_available(self, session, free_account):
        """**The half that keeps the gate a gate.**

        An account that already used its trial is denied, and the denial is the original one. If
        the retry were unconditional — or the trial assumed to have started — every Free account
        would get unlimited homes by hitting the limit twice.
        """
        from mihomes.services.property import EntitlementError, create_property

        free_account.trial_used_at = datetime(2026, 1, 1, tzinfo=UTC)
        session.commit()

        create_property(session, "Only Home")
        with pytest.raises(EntitlementError):
            create_property(session, "Second Home")


class TestExpiryIsNondestructive:
    def test_expiry_is_nondestructive(self, session, free_account):
        """**A19** — expiry downgrades and surfaces the over-limit state, dropping nothing.

        `PRICING` §4.3: *"we never delete data for a billing lapse."* An account that ran four
        homes on trial keeps all four — over-limit and read-only, never removed. Deleting the
        surplus is the intuitive tidy-up and the one thing the policy forbids.
        """
        from mihomes.cli.jobs import _expire_trial
        from mihomes.services.property import create_property

        start_trial(session, free_account)
        for name in ("Home One", "Home Two", "Home Three"):
            create_property(session, name)

        assert session.query(Property).count() == 3

        free_account.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()
        _expire_trial(session, free_account)

        assert free_account.plan == "free"
        assert session.query(Property).count() == 3, (
            "expiry must drop nothing — the surplus goes read-only (PRICING §4.3/A19)"
        )
        # And the over-limit state is *visible*, not silent: the next home is refused.
        assert isinstance(
            can(free_account, "property.add", {"current_homes": 3}), Denied
        )

    def test_expiry_clears_the_trialing_status(self, session, free_account):
        """**A Step 13 gap this test found.**

        `_expire_trial` set `plan="free"` and cleared `trial_ends_at`, but left
        `subscription_status="trialing"`. Nothing leaked — `trialing` maps to the account's own
        plan, which was now Free — but any code reading the status directly (a banner, the
        reconcile sweep, a webhook comparison) would have reported an ended trial as running.
        """
        from mihomes.cli.jobs import _expire_trial

        start_trial(session, free_account)
        free_account.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

        _expire_trial(session, free_account)

        assert free_account.subscription_status is None, (
            "an expired trial must not still read as `trialing`"
        )
        assert is_on_trial(free_account) is False

    def test_expiry_keeps_the_history(self, session, free_account):
        """A18 again, from the expiry side — the sweep must not reset the flag it depends on."""
        from mihomes.cli.jobs import _expire_trial

        start_trial(session, free_account)
        free_account.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

        _expire_trial(session, free_account)

        assert free_account.trial_used_at is not None
        assert start_trial(session, free_account) is False
