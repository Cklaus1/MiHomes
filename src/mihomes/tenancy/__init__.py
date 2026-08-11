"""Tenancy — the account boundary and the machinery that enforces it.

Public API for callers:

    from mihomes.tenancy import current_account, current_user   # ContextVars (G8)
    from mihomes.tenancy.registry import TENANT_TABLES          # the authority

The registry is deliberately explicit rather than derived from
`TenantOwned.__subclasses__()` — see `registry.py` for why that difference is a
security property and not a style choice.
"""

from mihomes.tenancy.context import (
    account_context,
    current_account,
    current_user,
    require_account,
    require_user,
)
from mihomes.tenancy.registry import (
    ASSOCIATION_TABLES,
    GLOBAL_TABLES,
    TENANT_TABLES,
    association_tables,
    check_registry,
    tenant_models,
)

# Imported for its import-time side effect: registering the before_flush listener
# that stamps account_id on insert. Importing `mihomes.tenancy` is therefore enough
# to get tenant enforcement — a caller cannot opt out by forgetting a setup call.
from mihomes.tenancy.session import install_tenant_listeners  # noqa: F401

__all__ = [
    "ASSOCIATION_TABLES",
    "GLOBAL_TABLES",
    "TENANT_TABLES",
    "account_context",
    "association_tables",
    "check_registry",
    "current_account",
    "current_user",
    "install_tenant_listeners",
    "require_account",
    "require_user",
    "tenant_models",
]
