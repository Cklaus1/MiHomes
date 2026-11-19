"""G7 · §6 Step 7 — status → entitlement behaviour (A2, A8).

**A2 is "all eight Stripe statuses map to `BILLING` §5's documented behaviour", and the eight is
the assertion.** Stripe's status set is fixed and public, so a test that checks the three
interesting ones would leave `incomplete_expired` and `paused` — the two nobody thinks about —
resolving by whatever default happened to be reachable. §5 is explicit that both must fail closed,
and "fails closed by accident, through two layers of default" is not the same claim as "fails
closed by decision".

The normalization half runs against the **real adapter**, not a fake: `_normalize_status` is where
an unknown future Stripe status is caught, and stubbing it would test the assertion rather than
the guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mihomes.entitlements.limits import PLAN_LIMITS_PHASE3, limits_for
from mihomes.services.billing.stripe_provider import _normalize_status

#: `BILLING` §5's table, transcribed as data so each row is asserted rather than described.
#:
#: The right-hand column is **which plan's entitlements apply**, not the stored plan — those are
#: different (see `apply_subscription_state`). `None` means "the account's own plan".
STATUS_TABLE = [
    # (vendor status, normalized, effective plan for a Pro account)
    ("trialing", "trialing", "pro"),
    ("active", "active", "pro"),
    ("past_due", "past_due", "pro"),      # grace: FULL access (D10)
    ("unpaid", "unpaid", "free"),         # dunning exhausted: restricted
    ("canceled", "canceled", "free"),
    ("incomplete", "none", "free"),       # checkout never completed
    ("incomplete_expired", "none", "free"),
    ("paused", "none", "free"),
]


class _Acct:
    """Minimal stand-in — `can()`/`limits_for` read two attributes and nothing else."""

    def __init__(self, plan: str, status: str | None) -> None:
        self.plan = plan
        self.subscription_status = status


class TestTheEightStatuses:
    @pytest.mark.parametrize("vendor,normalized,_effective", STATUS_TABLE)
    def test_normalization(self, vendor, normalized, _effective):
        """Each vendor status normalizes exactly as §5's rule states."""
        assert _normalize_status(vendor) == normalized

    @pytest.mark.parametrize("vendor,_normalized,effective", STATUS_TABLE)
    def test_status_table(self, vendor, _normalized, effective):
        """**A2** — each status resolves to the documented entitlement behaviour.

        Asserted through `limits_for` against the **Phase 3** table, because that is what the
        product will actually resolve against and the numbers differ per plan. A Pro account is
        used throughout so "keeps Pro" and "drops to Free" are distinguishable — on a Free
        account every row would look identical and the test would prove nothing.
        """
        status = _normalize_status(vendor)
        limits = limits_for("pro", status, table=PLAN_LIMITS_PHASE3)
        expected = PLAN_LIMITS_PHASE3[effective]

        assert limits["max_homes"] == expected["max_homes"], (
            f"{vendor!r} must resolve to {effective} entitlements (BILLING §5)"
        )
        assert limits["ai_calls_per_month"] == expected["ai_calls_per_month"]

    def test_the_table_covers_every_status_stripe_sends(self):
        """The set itself, so a missing row cannot hide behind the parametrisation.

        Stripe's subscription statuses are a closed, documented set. Enumerating them here and
        asserting the table matches is what makes A2's "all eight" checkable rather than
        aspirational.
        """
        stripe_statuses = {
            "trialing", "active", "past_due", "unpaid",
            "canceled", "incomplete", "incomplete_expired", "paused",
        }
        assert {row[0] for row in STATUS_TABLE} == stripe_statuses


class TestFailClosed:
    def test_unknown_status_fails_closed_to_free(self):
        """**§5's rule for a status this code predates.**

        *"Any unknown future vendor status normalizes to `none` (fail closed to Free
        entitlements, never to paid access) and logs loudly."* Stripe adds statuses; the failure
        mode worth preventing is a new one silently reading as paid access.
        """
        assert _normalize_status("some_future_status") == "none"
        assert limits_for("pro", "none", table=PLAN_LIMITS_PHASE3)["max_homes"] == (
            PLAN_LIMITS_PHASE3["free"]["max_homes"]
        )

    def test_unknown_status_logs_loudly(self, caplog):
        """The log half of the same rule, and it is not decoration.

        Failing closed silently means a customer paying for Estate quietly gets Free and nobody
        finds out until they complain. The warning is how a new Stripe status becomes a
        five-minute fix instead of a support ticket.
        """
        import logging

        with caplog.at_level(logging.WARNING):
            _normalize_status("brand_new_status")
        # `getMessage()`, not `record.message % record.args` — pytest's caplog handler has
        # already applied the args, so re-formatting raises on the second pass.
        assert any("brand_new_status" in r.getMessage() for r in caplog.records)

    def test_no_subscription_is_none_not_a_string(self):
        """D4 — a Free account has no Stripe subscription at all, and `None` passes through.

        Distinct from `"none"`: the string is a *normalized status* meaning "Stripe says this
        subscription is not active", while `None` means "there is no subscription object". The
        first is a fact about a subscription; the second is the absence of one.
        """
        assert _normalize_status(None) is None


class TestGraceVersusRestricted:
    def test_grace_then_restrict(self, ):
        """**A8** — `past_due` retains full access; `unpaid` restricts.

        The product decision behind D10, and the one most likely to be "simplified" later:
        `past_due` means a card failed and Stripe is retrying. Locking a household out of its own
        home over a payment retry — when the fix may be a card that expired last week — is worse
        for the customer *and* worse for recovery, since a locked-out user is less likely to come
        back and update it.
        """
        past_due = limits_for("pro", "past_due", table=PLAN_LIMITS_PHASE3)
        assert past_due["max_homes"] == PLAN_LIMITS_PHASE3["pro"]["max_homes"], (
            "past_due is the grace window — full access is retained while dunning runs (D10)"
        )

        unpaid = limits_for("pro", "unpaid", table=PLAN_LIMITS_PHASE3)
        assert unpaid["max_homes"] == PLAN_LIMITS_PHASE3["free"]["max_homes"], (
            "unpaid means dunning is exhausted — entitlements drop to Free (PRICING §4.3)"
        )

    def test_the_two_are_actually_different(self):
        """Guard on A8: if Pro and Free happened to share a limit, the test above would pass
        while proving nothing about the distinction."""
        assert (
            PLAN_LIMITS_PHASE3["pro"]["max_homes"]
            != PLAN_LIMITS_PHASE3["free"]["max_homes"]
        )


class TestApplySubscriptionState:
    def test_plan_and_status_are_both_stored(self, session, account_a):
        from mihomes.models.account import Account
        from mihomes.services.billing.provider import SubscriptionState
        from mihomes.services.billing.service import apply_subscription_state

        account = session.get(Account, account_a)
        changed = apply_subscription_state(
            session, account,
            SubscriptionState(
                provider_subscription_id="sub_1", plan="pro", status="active",
                current_period_end=datetime(2026, 12, 1, tzinfo=UTC),
                cancel_at_period_end=False,
            ),
        )

        assert changed is True
        assert account.plan == "pro"
        assert account.subscription_status == "active"
        assert account.stripe_subscription_id == "sub_1"

    def test_a_cancelled_account_keeps_its_plan_string(self, session, account_a):
        """**`PRICING` §4.3's "nothing was deleted", applied to the plan column.**

        A cancelled Pro account stays `plan="pro"` with `subscription_status="canceled"`.
        Entitlements already resolve that pair to Free via `limits_for`, so clearing the plan too
        would be a second mechanism doing the same job — and the one that forgets what to restore
        when the customer pays again, which §4.3 promises is instant.
        """
        from mihomes.models.account import Account
        from mihomes.services.billing.provider import SubscriptionState
        from mihomes.services.billing.service import apply_subscription_state

        account = session.get(Account, account_a)
        account.plan = "pro"
        account.subscription_status = "active"

        apply_subscription_state(
            session, account,
            SubscriptionState(
                provider_subscription_id="sub_1", plan="pro", status="canceled",
                current_period_end=None, cancel_at_period_end=True,
            ),
        )

        assert account.plan == "pro", "the stored plan is what reactivation restores"
        assert account.subscription_status == "canceled"
        assert limits_for(
            account.plan, account.subscription_status, table=PLAN_LIMITS_PHASE3
        )["max_homes"] == PLAN_LIMITS_PHASE3["free"]["max_homes"], (
            "entitlements still drop to Free — the plan string is memory, not access"
        )

    def test_a_null_plan_does_not_downgrade(self, session, account_a):
        """**The one that would cost real money.**

        `invoice.paid` carries no line items, so its `SubscriptionState.plan` is `None` — and a
        price id absent from `PRICE_ENV_VARS` also normalizes to `None`, because
        `plan_for_price_id` refuses to guess. Treating either as Free would downgrade a paying
        customer **on an ordinary receipt**, which is the most routine event Stripe sends.
        """
        from mihomes.models.account import Account
        from mihomes.services.billing.provider import SubscriptionState
        from mihomes.services.billing.service import apply_subscription_state

        account = session.get(Account, account_a)
        account.plan = "estate"
        account.subscription_status = "active"

        apply_subscription_state(
            session, account,
            SubscriptionState(
                provider_subscription_id=None, plan=None, status="active",
                current_period_end=None, cancel_at_period_end=False,
            ),
        )

        assert account.plan == "estate", (
            "plan=None means 'unknown from this event', never 'Free' — an invoice.paid must not "
            "downgrade a paying customer"
        )

    def test_no_change_returns_false(self, session, account_a):
        """`reconcile` reports "drift corrected" from this return value, so an unchanged
        account must say so — otherwise every sweep would look like it found a problem."""
        from mihomes.models.account import Account
        from mihomes.services.billing.provider import SubscriptionState
        from mihomes.services.billing.service import apply_subscription_state

        account = session.get(Account, account_a)
        state = SubscriptionState(
            provider_subscription_id="sub_x", plan="pro", status="active",
            current_period_end=None, cancel_at_period_end=False,
        )

        assert apply_subscription_state(session, account, state) is True
        assert apply_subscription_state(session, account, state) is False
