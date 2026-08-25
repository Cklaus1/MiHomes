"""G8 · §6 Step 8 — real limits (A1, A3). **The exit criterion's first half.**

A1 is *"Free denies a 2nd home, a 4th seat, and a staff invite"*, and each of the three is a
different mechanism: a counted limit read from context, a second counted limit, and a boolean
key. A test covering one would leave the other two resolving by whatever path happened to work.

**These tests resolve against the live binding, deliberately.** Everywhere else in this suite that
needs the real numbers passes `table=PLAN_LIMITS_PHASE3` explicitly, which is right for testing
the *machinery* and wrong here: Step 8's entire content is *which table is active*, so a test that
names its table cannot detect the thing being shipped. Flipping the binding back must turn these
red — verified by mutation, since a permissive fixture default could otherwise make A1 vacuous.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mihomes.entitlements.limits import PLAN_LIMITS, UPGRADE_PATH
from mihomes.entitlements.service import Allowed, Denied, can


@dataclass
class FakeAccount:
    plan: str = "free"
    subscription_status: str | None = None


class TestFreeGates:
    def test_free_gates(self):
        """**A1** — Free denies a 2nd home, a 4th seat, and a staff invite.

        All three in one test because A1 states them as one criterion, and each exercises a
        different code path: `max_homes` and `max_seats` are counted limits resolved against a
        caller-supplied count, `staff_invites_allowed` is a boolean key.
        """
        free = FakeAccount(plan="free")

        assert isinstance(can(free, "property.add", {"current_homes": 1}), Denied), (
            "Free allows 1 home (§3.1) — the 2nd is the upgrade trigger"
        )
        assert isinstance(can(free, "seat.add", {"current_seats": 3}), Denied), (
            "Free allows 3 seats — the 4th is the upgrade trigger"
        )
        assert isinstance(can(free, "invite.staff"), Denied), (
            "Free excludes the staff role entirely"
        )

    def test_free_allows_what_it_includes(self):
        """The positive control, and it is not decoration.

        A gate that denied everything would pass `test_free_gates` completely while making the
        product unusable at signup — no first home, no first two colleagues. The boundary is the
        assertion: *at* the limit is allowed, *past* it is denied.
        """
        free = FakeAccount(plan="free")

        assert isinstance(can(free, "property.add", {"current_homes": 0}), Allowed)
        assert isinstance(can(free, "seat.add", {"current_seats": 2}), Allowed)

    @pytest.mark.parametrize("plan", ["pro", "estate"])
    def test_paid_plans_allow_what_free_denies(self, plan):
        """The upgrade actually buys something — otherwise the paywall is a dead end that takes
        money and changes nothing."""
        paid = FakeAccount(plan=plan, subscription_status="active")

        assert isinstance(can(paid, "property.add", {"current_homes": 1}), Allowed)
        assert isinstance(can(paid, "seat.add", {"current_seats": 3}), Allowed)
        assert isinstance(can(paid, "invite.staff"), Allowed)


class TestUpgradeTargets:
    def test_denied_names_target(self):
        """**A3** — every `Denied` names the plan that would allow it (`PRICING` rule 4).

        Without it the UI has nothing to offer and the paywall becomes a dead end: the user is
        told no, with no path to yes. Asserted on all three of A1's denials rather than one,
        because each builds its `Denied` at a different point in `can()`.
        """
        free = FakeAccount(plan="free")

        for action, context in [
            ("property.add", {"current_homes": 1}),
            ("seat.add", {"current_seats": 3}),
            ("invite.staff", None),
        ]:
            decision = can(free, action, context)
            assert isinstance(decision, Denied)
            assert decision.upgrade_target, (
                f"{action} denied with no upgrade target — rule 4 requires the Denied to name "
                "the plan that would allow it"
            )
            assert decision.upgrade_target in PLAN_LIMITS, (
                f"{action} points at {decision.upgrade_target!r}, which is not a real plan"
            )

    def test_the_named_target_actually_allows_it(self):
        """**The half that makes A3 mean something.**

        A `Denied` naming *a* plan is cheap; naming one that would still deny is worse than
        naming none — the user pays and hits the same wall. So the target is resolved and the
        same action re-asked against it.
        """
        free = FakeAccount(plan="free")
        decision = can(free, "invite.staff")

        upgraded = FakeAccount(plan=decision.upgrade_target, subscription_status="active")
        assert isinstance(can(upgraded, "invite.staff"), Allowed), (
            f"{decision.upgrade_target} was offered as the fix but still denies the action"
        )

    def test_an_estate_only_key_points_past_pro(self):
        """Walking the chain, not returning the next plan blindly.

        `predictive_maintenance` is Estate-only, so a Free user denied it must be pointed at
        **estate** — pointing at pro would deny them again after they paid, which is the specific
        failure `_upgrade_target`'s loop exists to prevent.
        """
        free = FakeAccount(plan="free")
        decision = can(free, "maintenance.predict")

        assert isinstance(decision, Denied)
        assert decision.upgrade_target == "estate"

    def test_the_top_plan_names_no_target(self):
        """`UPGRADE_PATH["estate"]` is `None`, and that is a different statement from "nobody
        filled this in" — there is genuinely no higher plan to sell."""
        assert UPGRADE_PATH["estate"] is None


class TestStatusIsAnInput:
    def test_an_unpaid_pro_account_is_gated_as_free(self):
        """D5 — billing status is an input to `can()`, not a parallel gate.

        A Pro account whose dunning is exhausted resolves to Free entitlements while keeping its
        `plan` string, so the same call that succeeded last month now denies. This is where the
        status mapping (G7) and the limits table meet.
        """
        unpaid = FakeAccount(plan="pro", subscription_status="unpaid")
        assert isinstance(can(unpaid, "property.add", {"current_homes": 1}), Denied)

    def test_a_past_due_pro_account_is_not_gated(self):
        """D10 — `past_due` is the grace window and keeps full access.

        Paired with the test above so the *distinction* is asserted, not just one side of it: if
        both statuses behaved the same, one of these two tests would fail.
        """
        past_due = FakeAccount(plan="pro", subscription_status="past_due")
        assert isinstance(can(past_due, "property.add", {"current_homes": 1}), Allowed)


class TestTheCallerMustSupplyTheCount:
    def test_a_missing_count_fails_closed(self):
        """`can()` never queries the count itself — rule 5 requires the check to fire inside the
        caller's transaction, and a service that counted here would read outside it and
        reintroduce the race the gate exists to prevent.

        So a caller that forgets the count is **denied**, not treated as zero. Failing open here
        would be the quietest possible way to give the product away.
        """
        free = FakeAccount(plan="free")
        assert isinstance(can(free, "property.add"), Denied)
        assert isinstance(can(free, "seat.add", {}), Denied)
