"""G4 · §6 Step 3 — the entitlements service (A25, A26).

`SAAS_PRD:144`: shipping this in Phase 2 is what *"prevents Phase 2 secretly depending on Phase
3."* The interface is real now; Phase 3 supplies billing state and swaps which limits table is
active.

**The two-table design is what makes A25 testable without breaking D18.** Phase 2's active table
says "free, unlimited" (§7's deferred table), so `can()` never denies in production — which is
correct and is what N8/D18 require. Testing A25 against that table would be vacuous, so the
denial machinery is exercised against `PLAN_LIMITS_PHASE3`, the real §3.1 numbers, which Phase 3
will make active.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mihomes.entitlements.limits import (
    ENTITLEMENT_KEYS,
    PLAN_LIMITS,
    PLAN_LIMITS_PHASE3,
    limits_for,
)
from mihomes.entitlements.service import Allowed, Denied, can, usage


@dataclass
class FakeAccount:
    """A stand-in for `Account` — `can()` reads only `plan` and `subscription_status`.

    Deliberately not the ORM model: these are pure config decisions, and binding them to a
    database row would make the rules harder to read and slower to test than they deserve.
    """

    plan: str = "free"
    subscription_status: str | None = None


class TestLimitsTables:
    def test_tables_declare_identical_keys(self):
        """Rule 1 — one source of truth, and the two tables must not drift.

        Phase 3's change is a swap of which table is active. A key present in one and missing
        from the other would silently resolve to `None` after that swap and read as "not
        entitled" — or worse, as falsy-but-allowed depending on the call site.
        """
        for name, table in (("PLAN_LIMITS", PLAN_LIMITS), ("PHASE3", PLAN_LIMITS_PHASE3)):
            for plan, limits in table.items():
                assert set(limits) == ENTITLEMENT_KEYS, (
                    f"{name}[{plan}] key set differs from the canonical §3.1 keys: "
                    f"missing={sorted(ENTITLEMENT_KEYS - set(limits))} "
                    f"extra={sorted(set(limits) - ENTITLEMENT_KEYS)}"
                )

    def test_phase3_table_matches_the_pricing_doc(self):
        """Spot-check the numbers `PRICING` §3.1 states as non-placeholder.

        Free's `1 home / 3 seats` are the only values the doc marks as **not** placeholder, so
        they are the ones worth pinning: a typo there would misprice the product.
        """
        free = PLAN_LIMITS_PHASE3["free"]
        assert free["max_homes"] == 1
        assert free["max_seats"] == 3
        assert free["staff_invites_allowed"] is False

    def test_the_active_table_gates_free(self):
        """**Rewritten at SPEC-004 Step 8 — this test asserted the inverse.**

        It was `test_phase2_table_gates_nothing`, pinning D18/N8's *"Nothing flips"*: Free had
        unlimited homes, staff invites allowed, ratings on. That was correct for Phase 2, where
        every account was `free` and gating the product would have locked out every user with no
        paid tier to upgrade to — and pinning it made Phase 3's activation a visible, deliberate
        diff instead of something that quietly already happened.

        **This is that diff.** Rewritten rather than deleted, so the change of intent is legible
        in the history: the same three values, now asserted the other way, are exactly what
        "Phase 3 turns the gates on" means.
        """
        free = PLAN_LIMITS["free"]
        assert free["staff_invites_allowed"] is False, (
            "§3.1: Free does not include the staff role — the gate is live as of Phase 3"
        )
        assert free["max_homes"] == 1, "§3.1's one non-placeholder number"
        assert free["vendor_ratings"] is False, "D12: enforced per the PRD, superseding N8"

    def test_unlimited_is_a_ceiling_not_infinity(self):
        """`PRICING` §3.1's note: a real ceiling still catches runaway cost and abuse.

        `float("inf")` would disable the alerting the ceiling exists to trigger.
        """
        from mihomes.entitlements.limits import UNLIMITED

        assert isinstance(UNLIMITED, int)
        assert UNLIMITED != float("inf")


class TestBillingStatusIsAnInput:
    """Rule 3 — the same plan behaves differently by billing status."""

    @pytest.mark.parametrize("status", ["trialing", "active", "past_due"])
    def test_full_entitlements_during_grace(self, status):
        """`past_due` keeps full entitlements during grace — a product decision, not an
        oversight: do not lock a household out of its own home over a failed card."""
        limits = limits_for("pro", status, table=PLAN_LIMITS_PHASE3)
        assert limits["max_homes"] == PLAN_LIMITS_PHASE3["pro"]["max_homes"]

    @pytest.mark.parametrize("status", ["unpaid", "canceled", "incomplete"])
    def test_restricted_statuses_fall_back_to_free(self, status):
        limits = limits_for("pro", status, table=PLAN_LIMITS_PHASE3)
        assert limits["max_homes"] == PLAN_LIMITS_PHASE3["free"]["max_homes"]

    def test_unknown_plan_or_status_fails_closed_to_free(self):
        """Rule 2's fail-closed direction: a garbled plan string must not read as an
        entitlement."""
        assert limits_for("enterprise-gold", None, table=PLAN_LIMITS_PHASE3)["max_homes"] == 1
        assert limits_for("pro", "who-knows", table=PLAN_LIMITS_PHASE3)["max_homes"] == 1


class TestCanContract:
    def test_denied_names_target(self):
        """A25 · rule 4 — every `Denied` names the plan that would allow it."""
        free = FakeAccount(plan="free")
        decision = can(free, "invite.staff", table=PLAN_LIMITS_PHASE3)

        assert isinstance(decision, Denied)
        assert decision.upgrade_target == "pro"
        assert decision.reason

    def test_upgrade_target_skips_a_plan_that_would_also_deny(self):
        """`predictive_maintenance` is Estate-only, so a Free user must be pointed at **estate**.

        Returning `UPGRADE_PATH["free"]` blindly would say "pro" and deny them again after they
        paid — the worst possible upgrade prompt.
        """
        decision = can(
            FakeAccount(plan="free"), "maintenance.predict", table=PLAN_LIMITS_PHASE3
        )
        assert isinstance(decision, Denied)
        assert decision.upgrade_target == "estate"

    def test_no_upgrade_target_when_no_plan_would_allow(self):
        """`None` here means "nothing to sell", which is a different claim from "unfilled" —
        and is why `upgrade_target` is a required field rather than a defaulted one."""
        decision = can(
            FakeAccount(plan="estate"), "maintenance.predict", table=PLAN_LIMITS_PHASE3
        )
        assert isinstance(decision, Allowed)

    def test_counted_action_allows_below_the_limit(self):
        decision = can(
            FakeAccount(plan="free"), "property.add", {"current_homes": 0},
            table=PLAN_LIMITS_PHASE3,
        )
        assert isinstance(decision, Allowed)

    def test_counted_action_denies_at_the_limit(self):
        decision = can(
            FakeAccount(plan="free"), "property.add", {"current_homes": 1},
            table=PLAN_LIMITS_PHASE3,
        )
        assert isinstance(decision, Denied)
        assert decision.upgrade_target == "pro"

    def test_missing_count_fails_closed(self):
        """Rule 5 requires the caller to pass its in-transaction count.

        A caller that forgets must not be read as "zero used" — that would turn the race-proof
        check into a permanent allow, which is exactly the seat race A19 tests.
        """
        decision = can(FakeAccount(plan="free"), "property.add", {}, table=PLAN_LIMITS_PHASE3)
        assert isinstance(decision, Denied)

    def test_decisions_are_truthy_and_falsy(self):
        """`if can(...)` is the shape call sites will reach for; make it mean the right thing."""
        assert can(FakeAccount(), "anything.ungated")
        assert not can(FakeAccount(plan="free"), "invite.staff", table=PLAN_LIMITS_PHASE3)

    def test_ungated_action_is_allowed(self):
        """Entitlements answer only about the keys §3.1 declares.

        Denying unknown actions would make every RBAC-only action require an entitlements entry,
        collapsing D10's two independent gates into one.
        """
        assert isinstance(can(FakeAccount(), "task.manage"), Allowed)

    def test_the_active_table_denies_the_free_to_pro_triggers(self):
        """**A1** — with the *active* table, `can()` denies the Free→Pro triggers.

        **Rewritten at Step 8**, and its previous name says what changed:
        `test_phase2_active_table_denies_nothing` was the assertion that *"would fail the day
        someone activates the Phase 3 numbers without meaning to"*. Step 8 activates them on
        purpose, so the test now asserts the activation rather than guarding against it.

        Denials are checked with `isinstance`, not truthiness: `Denied.__bool__` returns `False`,
        so `assert not can(...)` would also pass on `None` or on a decision object that forgot
        its type — and both would be bugs this test exists to catch.
        """
        free = FakeAccount(plan="free")

        assert isinstance(can(free, "invite.staff"), Denied), (
            "Free excludes the staff role (§3.1)"
        )
        assert isinstance(can(free, "property.add", {"current_homes": 1}), Denied), (
            "Free allows one home; the second is the upgrade trigger"
        )
        # The first home must still be creatable — a gate that denied *every* home would pass
        # the two assertions above while making the product unusable on signup.
        assert isinstance(can(free, "property.add", {"current_homes": 0}), Allowed)


class TestIndependentGates:
    def test_both_gates_required(self):
        """A26 · D10 — RBAC and entitlements are separate, and both must pass.

        Demonstrated in both directions, because a single-direction test would pass against an
        implementation that simply always allowed one of them:

        - the plan allows what the role forbids (staff on Estate still cannot view finances);
        - the role allows what the plan forbids (an owner on Free still hits the seat cap).
        """
        from mihomes.authz.actions import MATRIX, Grant

        # Plan says yes, role says no.
        estate = FakeAccount(plan="estate")
        assert can(estate, "vendor.rate", table=PLAN_LIMITS_PHASE3)
        assert MATRIX["finance.view"].staff is Grant.DENY

        # Role says yes, plan says no.
        assert MATRIX["invite.create"].owner is Grant.ALLOW
        assert not can(FakeAccount(plan="free"), "invite.staff", table=PLAN_LIMITS_PHASE3)

    def test_entitlements_do_not_consult_the_matrix(self):
        """The separation, structurally: `can()` takes no role and cannot consult one.

        If it did, a plan allowance could bypass a role denial — or vice versa — and D10's
        "a permission grant never bypasses a plan limit" would depend on remembering to check
        twice rather than on the two gates being unable to see each other.
        """
        import inspect

        signature = inspect.signature(can)
        assert "role" not in signature.parameters
        assert "principal" not in signature.parameters


class TestCanIsActuallyCalled:
    """§6 Step 3's verify clause — *"`can()` is called at invite creation and property creation
    server-side"*.

    A service that ships and is never called is not a gate; it is a module. The invite half lands
    with Step 12 (`invite_service` does not exist yet, and A19's seat race is its own gate), so
    only property creation is asserted here.
    """

    def test_property_creation_consults_entitlements(self, session, account_a, monkeypatch):
        """The call must happen, and it must carry the in-transaction count (rule 5).

        Asserting on the *arguments* rather than merely that creation succeeds is the point: with
        Phase 2's permissive table, a `can()` that was never called and one that was called and
        allowed are indistinguishable by outcome.
        """
        import mihomes.entitlements as entitlements
        from mihomes.services.property import create_property

        seen = {}
        real_can = entitlements.can

        def spy(account, action, context=None, **kwargs):
            seen["action"] = action
            seen["context"] = context
            return real_can(account, action, context, **kwargs)

        monkeypatch.setattr("mihomes.entitlements.can", spy)

        create_property(session, "Belle Estate")

        assert seen["action"] == "property.add"
        assert "current_homes" in seen["context"], (
            "the count must be passed from inside the caller's transaction (PRICING rule 5)"
        )

    def test_property_creation_is_not_gated_in_phase_2(self, session, account_a):
        """D18/N8 — the call fires and allows. Creating a second property must still work.

        This is the assertion that fails the day the Phase 3 table is activated by accident.
        """
        from mihomes.services.property import create_property

        create_property(session, "Belle Estate")
        create_property(session, "Blue Room")


class TestUsageIsMeasured:
    def test_usage_reports_the_real_limit(self):
        """**Rewritten at SPEC-004 Step 11 — this test asserted the inverse.**

        It was `test_usage_is_declared_only`, pinning P3-b/N9: *"no meter exists; `usage()` is an
        interface, not a measurement"*, with `limit=None` because reporting a limit while nothing
        counted toward it would render as "0 of 200 used" instead of "not measured".

        The meter exists now (Step 10), so the limit is a measurement and `None` would be the
        lie. Rewritten rather than deleted, keeping the old name in this docstring, so the change
        of intent is legible — the same precedent as Step 8's two table tests.

        `used` is still 0 here because no session is passed: the two-argument signature SPEC-003
        §5.3 requires *"character-for-character"* has nothing to read from. The real
        measurement is covered by `test_overage.py::test_usage_returns_a_real_measurement`.
        """
        report = usage(FakeAccount(plan="free"), "ai_calls")
        assert report.used == 0
        assert report.limit == PLAN_LIMITS["free"]["ai_calls_per_month"]
