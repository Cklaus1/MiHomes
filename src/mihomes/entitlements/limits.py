"""Plan → limits, the single source of truth — `PRICING` §3.1, rule 1.

Rule 1: *"Plan → entitlements mapping lives in one config module, not scattered across
features."* Every gate resolves against this file; no feature hardcodes a plan name.

**Two tables, and the second one is the whole of D18.** `PRICING` §3.1 is the *Phase 3* table —
`max_homes: 1`, `staff_invites_allowed: false` for Free. Phase 2 makes **every account `free`**
(D7), so shipping those numbers as the active table would gate the entire product for every user
with no paid tier to upgrade to. SPEC-003 §7's deferred table says exactly what to do instead:
*"`can()` exists and is called; the limits config simply says 'free, unlimited'."*

So `PLAN_LIMITS` (active) is permissive, `PLAN_LIMITS_PHASE3` (declared, inert) carries §3.1's
real numbers, and `test_tables_declare_identical_keys` asserts the two never drift. Phase 3's
change is then a **one-line swap of which table is active**, not a rewrite — and a key added to
one table without the other fails the suite rather than silently defaulting.

N8 is the same decision seen from the feature side: `vendor_ratings` and `work_order_scheduling`
stay declared and wired to nothing until Phase 3 supplies billing state.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ENTITLEMENT_KEYS",
    "PLAN_LIMITS",
    "PLAN_LIMITS_PHASE3",
    "UNLIMITED",
    "UPGRADE_PATH",
    "limits_for",
]

# `unlimited` is a high soft ceiling, never literal infinity (`PRICING` §3.1 note): a real
# ceiling still protects against runaway AI cost and abuse, and `float("inf")` would silently
# disable the alerting that ceiling exists to trigger.
UNLIMITED = 10**9

# Which plan a denial should point at. `PRICING` rule 4: every `Denied` names the plan that
# would allow it, so the UI can render the right upgrade prompt.
UPGRADE_PATH = {"free": "pro", "pro": "estate", "estate": None}


# `PRICING` §3.1, verbatim. **Inert in Phase 2** — see the module docstring.
PLAN_LIMITS_PHASE3: dict[str, dict[str, Any]] = {
    "free": {
        "max_homes": 1,
        "max_seats": 3,
        "staff_invites_allowed": False,
        "roles_allowed": frozenset({"owner", "admin"}),
        "ai_calls_per_month": 200,
        "ai_overage_buffer_pct": 0,
        "ai_priority": "standard",
        "vendor_ratings": False,
        "work_order_scheduling": False,
        "predictive_maintenance": False,
        "weekly_ai_report": False,
        "audit_export": False,
        "support_tier": "community",
    },
    "pro": {
        "max_homes": 5,
        "max_seats": 10,
        "staff_invites_allowed": True,
        "roles_allowed": frozenset({"owner", "admin", "staff"}),
        "ai_calls_per_month": 3_000,
        "ai_overage_buffer_pct": 20,
        "ai_priority": "standard",
        "vendor_ratings": True,
        "work_order_scheduling": True,
        "predictive_maintenance": False,
        "weekly_ai_report": False,
        "audit_export": False,
        "support_tier": "email",
    },
    "estate": {
        "max_homes": UNLIMITED,
        "max_seats": 50,
        "staff_invites_allowed": True,
        "roles_allowed": frozenset({"owner", "admin", "staff"}),
        "ai_calls_per_month": 15_000,
        "ai_overage_buffer_pct": 20,
        "ai_priority": "priority",
        "vendor_ratings": True,
        "work_order_scheduling": True,
        "predictive_maintenance": True,
        "weekly_ai_report": True,
        "audit_export": True,
        "support_tier": "priority",
    },
}

ENTITLEMENT_KEYS = frozenset(PLAN_LIMITS_PHASE3["free"])


def _phase2_free() -> dict[str, Any]:
    """Free, unlimited — SPEC-003 §7's deferred table, D18, N8.

    Every key from §3.1 is present so the *shape* is final and call sites written now keep
    working when Phase 3 swaps the table. Only the values are permissive.
    """
    return {
        "max_homes": UNLIMITED,
        "max_seats": UNLIMITED,
        # §1.4: "Staff invites work in Phase 2 precisely because nothing gates them yet."
        "staff_invites_allowed": True,
        "roles_allowed": frozenset({"owner", "admin", "staff"}),
        "ai_calls_per_month": UNLIMITED,
        "ai_overage_buffer_pct": 0,
        "ai_priority": "standard",
        # N8: declared, wired to nothing until Phase 3.
        "vendor_ratings": True,
        "work_order_scheduling": True,
        "predictive_maintenance": True,
        "weekly_ai_report": True,
        "audit_export": True,
        "support_tier": "community",
    }


# Active in Phase 2. All three plans resolve permissively because D7 makes every account `free`
# and D18 defers the gates; Phase 3 replaces this binding with `PLAN_LIMITS_PHASE3`.
PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "free": _phase2_free(),
    "pro": _phase2_free(),
    "estate": _phase2_free(),
}


# `BILLING_AND_EMAIL` §5, quoted in `PRICING` rule 3: status → which plan's entitlements apply.
# `past_due` keeps full entitlements *during grace*, which is a product decision (do not lock a
# household out of its own home over a failed card) rather than an oversight.
_STATUS_TO_EFFECTIVE_PLAN = {
    "trialing": None,      # None = "the account's own plan"
    "active": None,
    "past_due": None,
    "unpaid": "free",      # restricted (§4.3)
    "canceled": "free",
    "incomplete": "free",
}


def limits_for(plan: str, subscription_status: str | None = None,
               table: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resolve the effective limits for a plan and billing status.

    `table` exists so Phase 3 — and the tests that prove the machinery works — can resolve
    against `PLAN_LIMITS_PHASE3` without the active binding changing meaning.

    An **unknown plan or status falls back to `free`**, which is rule 2's fail-closed direction
    for paid features: a garbled plan string must not be read as an entitlement.
    """
    table = table if table is not None else PLAN_LIMITS

    effective_plan = plan if plan in table else "free"
    if subscription_status is not None:
        override = _STATUS_TO_EFFECTIVE_PLAN.get(subscription_status, "free")
        if override is not None:
            effective_plan = override

    return table.get(effective_plan, table["free"])
