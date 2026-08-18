"""Authorization core — SPEC-003 Phase 2.

Phase 1 defends the boundary *between* customers; this package defends the boundary *inside* one.
That is the harder problem: cross-tenant leakage fails loudly and RLS backstops it, but a staff
member seeing another property's data — or the household's finances — looks exactly like the
feature working, and there is no backstop below this layer.

Public API grows as the phase lands; `require_permission`, `scoped_property_ids`, and
`redact_for_role` join it in their own groups.
"""

from mihomes.authz.actions import (
    ENTITY_CLASSES,
    EXTRA_RULES,
    MATRIX,
    Access,
    ActionSpec,
    EntityClass,
    Grant,
)

__all__ = [
    "ENTITY_CLASSES",
    "EXTRA_RULES",
    "MATRIX",
    "Access",
    "ActionSpec",
    "EntityClass",
    "Grant",
]
