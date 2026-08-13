"""Tenancy — the account boundary and the machinery that enforces it.

Public API for callers:

    from mihomes.tenancy import current_account, current_user   # ContextVars (G8)
    from mihomes.tenancy.registry import TENANT_TABLES          # the authority

The registry is deliberately explicit rather than derived from
`TenantOwned.__subclasses__()` — see `registry.py` for why that difference is a
security property and not a style choice.
"""

# `Base` is imported here rather than the drift guard being installed from
# `mihomes.models`, to keep the dependency one-way: registry imports models, so models
# must not import tenancy. See `install_drift_guard(...)` at the bottom of this module.
from mihomes.models import Base
from mihomes.tenancy.context import (
    account_context,
    current_account,
    current_user,
    require_account,
    require_user,
)
from mihomes.tenancy.drift_guard import install_drift_guard  # noqa: E402
from mihomes.tenancy.registry import (
    ASSOCIATION_TABLES,
    GLOBAL_TABLES,
    TENANT_TABLES,
    association_tables,
    check_registry,
    tenant_models,
)
from mihomes.tenancy.rls import install_rls  # noqa: E402

# Imported for its import-time side effect: registering the before_flush listener
# that stamps account_id on insert. Importing `mihomes.tenancy` is therefore enough
# to get tenant enforcement — a caller cannot opt out by forgetting a setup call.
from mihomes.tenancy.session import install_tenant_listeners  # noqa: F401

# Same import-time-side-effect reasoning as the listener above, for the G4 drift guard:
# attaching it to `Base.metadata` means `create_all` emits it, so the test suite's schema
# carries the same guard the migration installs. A guard present only in the migration
# would be absent from every test database, and the drift test would then pass against an
# unguarded schema.
install_drift_guard(Base.metadata)

# G7, same reasoning again: policies present in the migration but absent from the
# `create_all`-built test schema would let an RLS test pass against an unprotected table.
install_rls(Base.metadata)

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
