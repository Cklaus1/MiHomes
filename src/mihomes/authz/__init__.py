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
from mihomes.authz.query_scope import install_property_scope_listener
from mihomes.authz.redact import (
    MONEY_VISIBLE_TO_STAFF,
    REDACTED_FIELDS,
    money_columns,
    redact_for_role,
)
from mihomes.authz.scope import (
    authz_context,
    current_property_scope,
    current_role,
    property_scope,
    scoped_property_ids,
)

# **Installed here, not only in `web/deps.py`.** The listener was previously armed as a side
# effect of importing `authz.query_scope`, which the web layer does and nothing else did — so any
# other consumer of the scope (the Telegram bot's two DB paths, a CLI report, a background job)
# would bind a scope that **nothing read**, and fail open silently.
#
# That is F3's footgun wearing different clothes, and it is exactly why N2 insists the scope be
# impossible to forget. Arming it from the package root means reaching for `mihomes.authz` at all
# is enough.
install_property_scope_listener()

__all__ = [
    "ENTITY_CLASSES",
    "EXTRA_RULES",
    "MATRIX",
    "MONEY_VISIBLE_TO_STAFF",
    "REDACTED_FIELDS",
    "Access",
    "ActionSpec",
    "EntityClass",
    "Grant",
    "authz_context",
    "current_property_scope",
    "current_role",
    "money_columns",
    "property_scope",
    "redact_for_role",
    "scoped_property_ids",
]
