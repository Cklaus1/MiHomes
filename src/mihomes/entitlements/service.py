"""The entitlements service — `PRICING` §3.2's contract (A25, A26).

Two questions, per §3.2:

- `can(account, action, context) -> Allowed | Denied(reason, upgrade_target)`
- `usage(account, meter) -> {used, limit, resets_at}` — **DEFERRED (Phase 3)**, P3-b/N9.

**Why this ships in Phase 2 at all**, when D18 puts the gates in Phase 3: `SAAS_PRD:144` —
*"Without this, Phase 2 would secretly depend on Phase 3."* Building the interface now means
Phase 3 supplies billing state and flips a table, rather than retrofitting call sites into
fourteen finished features.

**D10: RBAC and entitlements are separate gates and both must pass.** Neither calls the other.
A permission grant never bypasses a plan limit, and a plan allowance never bypasses a role
denial — so a staff member on an Estate plan still cannot view finances, and an owner on Free
still cannot exceed the seat cap. Keeping them independent is what makes that true by
construction rather than by remembering to check twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mihomes.entitlements.limits import UPGRADE_PATH, limits_for

__all__ = ["Allowed", "Decision", "Denied", "UsageReport", "can", "usage"]


@dataclass(frozen=True)
class Allowed:
    """The action is permitted by the plan. Says nothing about RBAC (D10)."""

    limit: Any = None

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True)
class Denied:
    """The plan forbids the action.

    `upgrade_target` is **not optional** — `PRICING` rule 4: *"Every `Denied` names the plan that
    would allow it, so the UI can render the right upgrade prompt."* A `Denied` without one
    leaves the UI with nothing to offer, which is how a paywall becomes a dead end. It may still
    be `None` when *no* plan would allow the action, which is a different statement from "nobody
    filled this in" and is why the field is required rather than defaulted.
    """

    reason: str
    upgrade_target: str | None
    limit: Any = None

    def __bool__(self) -> bool:
        return False


Decision = Allowed | Denied


# action → the entitlement key it consults. Actions that count against a numeric limit carry the
# context key holding the current count, so `can()` never has to query — the caller does that
# inside its own transaction (rule 5: races cannot exceed a limit).
_BOOLEAN_ACTIONS = {
    "invite.staff": "staff_invites_allowed",
    "vendor.rate": "vendor_ratings",
    "work_order.schedule": "work_order_scheduling",
    "maintenance.predict": "predictive_maintenance",
    "report.weekly_ai": "weekly_ai_report",
    "audit.export": "audit_export",
}

_COUNTED_ACTIONS = {
    # action: (limit key, context key holding the current count)
    "property.add": ("max_homes", "current_homes"),
    "seat.add": ("max_seats", "current_seats"),
}


def can(
    account,
    action: str,
    context: dict | None = None,
    *,
    table: dict[str, dict[str, Any]] | None = None,
) -> Decision:
    """Is `action` allowed for this account's plan?

    `context` carries counts the caller already holds — `{"current_seats": 3}` — because rule 5
    requires the check to fire **inside the caller's transaction**, so two concurrent invites at
    the cap cannot both succeed. A service that queried the count itself would read outside that
    transaction and reintroduce the race it exists to prevent.

    `table` lets Phase 3 (and the tests that prove this machinery works) resolve against the real
    §3.1 numbers while Phase 2's active table stays permissive — see `limits.py`.
    """
    context = context or {}
    limits = limits_for(
        getattr(account, "plan", "free"),
        getattr(account, "subscription_status", None),
        table=table,
    )

    if action in _BOOLEAN_ACTIONS:
        key = _BOOLEAN_ACTIONS[action]
        if limits.get(key):
            return Allowed(limit=True)
        return Denied(
            reason=f"{action} is not available on the {account.plan} plan",
            upgrade_target=_upgrade_target(account, action, key, table),
            limit=False,
        )

    if action in _COUNTED_ACTIONS:
        limit_key, count_key = _COUNTED_ACTIONS[action]
        limit = limits.get(limit_key, 0)
        current = context.get(count_key)
        if current is None:
            # Fail closed: a caller that forgot to pass the count must not be read as "zero used".
            return Denied(
                reason=f"{action} requires {count_key!r} in context to check {limit_key}",
                upgrade_target=None,
                limit=limit,
            )
        if current < limit:
            return Allowed(limit=limit)
        return Denied(
            reason=f"{account.plan} allows {limit} ({limit_key}); {current} in use",
            upgrade_target=_upgrade_target(account, action, limit_key, table),
            limit=limit,
        )

    # An action the service does not gate is allowed — entitlements only answer about the keys
    # §3.1 declares. RBAC is the gate for everything else, and conflating the two would make
    # D10's separation untrue.
    return Allowed()


def _upgrade_target(account, action: str, key: str, table) -> str | None:
    """The next plan that would actually allow this, or `None` if none would.

    Walking the chain rather than returning `UPGRADE_PATH[plan]` blindly matters for
    Estate-only keys: a Free user denied `predictive_maintenance` must be pointed at **estate**,
    not at pro, which would deny them again after they paid.
    """
    from mihomes.entitlements.limits import PLAN_LIMITS_PHASE3

    resolution_table = table if table is not None else PLAN_LIMITS_PHASE3

    plan = getattr(account, "plan", "free")
    seen = set()
    while True:
        nxt = UPGRADE_PATH.get(plan)
        if nxt is None or nxt in seen:
            return None
        seen.add(nxt)
        candidate = resolution_table.get(nxt, {})
        value = candidate.get(key)
        if isinstance(value, bool):
            if value:
                return nxt
        elif isinstance(value, int) and value > resolution_table.get(plan, {}).get(key, 0):
            return nxt
        plan = nxt


@dataclass(frozen=True)
class UsageReport:
    used: int
    limit: int | None
    resets_at: None = None


def usage(account, meter: str) -> UsageReport:
    """**DEFERRED (Phase 3)** — a declared interface, not an enforced limit.

    P3-b: `ai_calls_per_month: 200` is unenforceable because **no meter exists anywhere in
    `src/`**. The only token record is `ai_conversations.tokens_used`, a nullable per-row int
    with no account and no monthly rollup. N9 is explicit: *"Do not build the AI usage meter."*

    Returning `limit=None` rather than the plan's number is deliberate — reporting `limit=200`
    while nothing counts toward it would let a caller render a usage bar that is always empty and
    read as "0 of 200 used" rather than "not measured". `None` cannot be mistaken for a
    measurement.
    """
    return UsageReport(used=0, limit=None, resets_at=None)
