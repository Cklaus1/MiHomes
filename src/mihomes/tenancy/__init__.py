"""Tenancy — the account boundary and the machinery that enforces it.

Public API for callers:

    from mihomes.tenancy import current_account, current_user   # ContextVars (G8)
    from mihomes.tenancy.registry import TENANT_TABLES          # the authority

The registry is deliberately explicit rather than derived from
`TenantOwned.__subclasses__()` — see `registry.py` for why that difference is a
security property and not a style choice.
"""

from mihomes.tenancy.registry import (
    ASSOCIATION_TABLES,
    GLOBAL_TABLES,
    TENANT_TABLES,
    association_tables,
    check_registry,
    tenant_models,
)

__all__ = [
    "ASSOCIATION_TABLES",
    "GLOBAL_TABLES",
    "TENANT_TABLES",
    "association_tables",
    "check_registry",
    "tenant_models",
]
