"""Entitlements — SPEC-003 §6 Step 3.

Ships in Phase 2 **config-only** (D7): every account is `free`, the limits table says
"free, unlimited" (§7's deferred table), and nothing flips (D18). What Phase 2 buys is the
*interface* — `SAAS_PRD:144`: *"Without this, Phase 2 would secretly depend on Phase 3."*

Separate from RBAC, and both gates must pass (D10).
"""

from mihomes.entitlements.limits import (
    ENTITLEMENT_KEYS,
    PLAN_LIMITS,
    PLAN_LIMITS_PHASE3,
    UNLIMITED,
    limits_for,
)
from mihomes.entitlements.service import (
    Allowed,
    Decision,
    Denied,
    UsageReport,
    can,
    check_entitlement,
    usage,
)

__all__ = [
    "ENTITLEMENT_KEYS",
    "PLAN_LIMITS",
    "PLAN_LIMITS_PHASE3",
    "UNLIMITED",
    "Allowed",
    "Decision",
    "Denied",
    "UsageReport",
    "can",
    "check_entitlement",
    "limits_for",
    "usage",
]
