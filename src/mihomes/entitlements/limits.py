"""Plan → limits, the single source of truth — `PRICING` §3.1, rule 1.

Rule 1: *"Plan → entitlements mapping lives in one config module, not scattered across
features."* Every gate resolves against this file; no feature hardcodes a plan name.

**One table, as of SPEC-004 Step 8.** Phase 2 shipped two: `PLAN_LIMITS` (permissive) active and
`PLAN_LIMITS_PHASE3` (§3.1's real numbers) declared but inert, because D7 made every account
`free` and gating the product with no paid tier to upgrade to would have locked out every user.
Phase 3 supplies billing state, so the real numbers become the active ones and the permissive
table is **deleted rather than left behind** — a second table nothing resolves against is a
loaded gun for the next reader, who cannot tell an inert fixture from a live one.

`PLAN_LIMITS_PHASE3` remains as an alias so existing call sites and tests keep working; it is the
same object, and `limits_for(table=...)` still takes an explicit table for tests that need to
resolve against something other than the live binding.

**These numbers are `PLACEHOLDER` except Free's 1 home / 3 seats** (SPEC-004 O1, blocks-ship).
They are literals rather than env vars deliberately — C11: a price id is deployment identity and
must be env, but the limits are product definition, already committed and drift-gated here, and
moving them to env would make the plan table unreadable for no safety gain.
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


# `PRICING` §3.1, verbatim. **Live as of Phase 3** — see the module docstring.
PLAN_LIMITS: dict[str, dict[str, Any]] = {
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
        "predictive_maintenance": True,
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

ENTITLEMENT_KEYS = frozenset(PLAN_LIMITS["free"])

#: Back-compatible alias — the same object, not a copy.
#:
#: Phase 2 code and tests refer to `PLAN_LIMITS_PHASE3` by name to mean "the real numbers". They
#: now *are* the active numbers, so the alias keeps those call sites correct instead of requiring
#: a rename that would say nothing new. `is` identity matters: a copy would reintroduce exactly
#: the drift the two-table arrangement was gated against.
#: Phase 3 overrides — features that roll out to lower tiers in a future release.
#:
#: These are merged on top of ``PLAN_LIMITS`` at runtime by the Phase 3 rollout
#: code (not yet implemented). Until then, they serve as a forward-looking
#: specification so that Phase 3 tests can assert the intended end-state.
PLAN_LIMITS_PHASE3_OVERRIDES: dict[str, dict[str, object]] = {
    "free": {
        "audit_export": True,
    },
    "pro": {
        "audit_export": True,
    },
}

#: Phase 3 plan limits — ``PLAN_LIMITS`` with Phase 3 overrides applied.
#:
#: This is the merged result used by Phase 3 tests and code.
PLAN_LIMITS_PHASE3: dict[str, dict[str, object]] = {
    tier: {**base, **PLAN_LIMITS_PHASE3_OVERRIDES.get(tier, {})}
    for tier, base in PLAN_LIMITS.items()
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


# Feature matrix — which plans get which add-on features.
PLAN_FEATURES: dict[str, dict[str, bool]] = {
    "free": {
        "predictive_maintenance": False,
        "audit_export": False,
        "advanced_analytics": False,
        "custom_workflows": False,
        "white_label": False,
    },
    "professional": {
        "predictive_maintenance": True,
        "audit_export": True,
        "advanced_analytics": True,
        "custom_workflows": True,
        "white_label": False,
    },
    "enterprise": {
        "predictive_maintenance": True,
        "audit_export": True,
        "advanced_analytics": True,
        "custom_workflows": True,
        "white_label": True,
    },
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


def check_entitlement(plan: str, feature: str,
                      subscription_status: str | None = None,
                      table: dict[str, dict[str, Any]] | None = None) -> bool:
    """Return True if *plan* includes *feature* for the given billing status.

    This is the single source of truth for plan-gated feature access.
    """
    table = table if table is not None else PLAN_FEATURES
    plan_table = table.get(plan, table["free"])
    return bool(plan_table.get(feature, False))
