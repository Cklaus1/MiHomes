"""The tenancy registry — which tables are account-scoped, and which are not.

**This module exists because `TenantOwned.__subclasses__()` is not a complete
answer, and trusting it would produce a silent cross-tenant leak.**

SPEC-002 §4.3 derives its RLS table list from the mixin's subclasses, and A1/A21
iterate the same set. But two association tables — `staff_properties` and
`vendor_properties` — are Core `Table(...)` objects with no declarative class
(`models/staff.py`, `models/vendor.py`). A `@declared_attr` mixin cannot reach them,
so under a subclasses-only registry they would get:

  - no `account_id` column
  - no RLS policy generated for them
  - no coverage from A1 or A21

...while A21 — "the phase's definition of done" — reported green. A readable and
writable cross-tenant surface, invisible to the test built to catch exactly that.

So the registry is **explicit**. `TENANT_TABLES` is the authority; the derived set
is cross-checked against it and a mismatch is an error, not a silent widening. That
inverts the failure mode: forgetting to register a new table breaks a test, rather
than quietly exempting the table from isolation.
"""

from __future__ import annotations

__all__ = [
    "GLOBAL_TABLES",
    "TEST_ONLY_TABLES",
    "TENANT_TABLES",
    "association_tables",
    "check_registry",
    "tenant_models",
]

# Read or written BEFORE account context exists, so a tenant policy on any of these
# returns zero rows and breaks the thing it is protecting (SPEC-002 D3).
#
#   users     a person exists independent of any account
#   sessions  auth middleware reads this to DISCOVER the current account
#   waitlist  Phase 0 signup, owned by the alembic_landing/ tree (SPEC-001 D4)
#
# `accounts` is excluded for a different reason: it is the tenant ROOT. It has no
# account_id of its own — it is what account_id points at.
GLOBAL_TABLES = frozenset({"users", "sessions", "waitlist", "accounts"})

# Not a real table: `tests/unit/test_slug.py` defines a throwaway `DummyModel` on the
# SHARED Base, so it appears in Base.metadata for every test in the session. Excluded
# by name rather than by a heuristic, so a genuinely unclassified table still fails
# `check_registry()` — the point of that function is that forgetting to classify a
# table is an error, and a clever filter would quietly re-open that hole.
TEST_ONLY_TABLES = frozenset({"dummy"})

# Every account-scoped table. Hardcoded on purpose — see the module docstring.
#
# 40, not the 37 SPEC-002 §6 estimates: the spec was written before Phase 0 and
# counted the domain tables only, so it omits `invites`, `memberships` and
# `membership_property_scopes`, which Step 1 itself adds.
TENANT_TABLES = frozenset({
    # --- domain ---------------------------------------------------------
    "ai_conversations",
    "alerts",
    "appointments",
    "asset_price_entries",
    "assets",
    "audit_log",
    "books",
    "budgets",
    "configurations",
    "consumable_price_entries",
    "consumables",
    "contracts",
    "documents",
    "event_guests",
    "events",
    "guests",
    "insurance_policies",
    "issues",
    "notes",
    "properties",
    "recurring_expenses",
    "spaces",
    "staff",
    "staff_pto_requests",
    "tag_assignments",
    "tags",
    "task_schedules",
    "tasks",
    "template_items",
    "templates",
    "transactions",
    "vendor_ratings",
    "vendors",
    "work_orders",
    "zones",
    # --- identity, added by Step 1 --------------------------------------
    "invites",
    "membership_property_scopes",
    "memberships",
    # --- Core association tables: NO declarative class ------------------
    # A mixin cannot reach these. `account_id` is declared by hand on the Table
    # objects themselves, and they are listed here so RLS generation and the
    # isolation test see them. This is the leak the registry exists to close.
    "staff_properties",
    "vendor_properties",
})

# The two above, named separately so callers that must special-case "no ORM class
# here" do not have to re-derive which they are.
ASSOCIATION_TABLES = frozenset({"staff_properties", "vendor_properties"})


def tenant_models() -> list[type]:
    """Mapped classes that are account-scoped.

    Does **not** cover the association tables — they have no class. Use
    `TENANT_TABLES` for anything that must be exhaustive (RLS generation, A21).
    """
    from mihomes.models import Base

    return [
        mapper.class_
        for mapper in Base.registry.mappers
        if mapper.class_.__tablename__ in TENANT_TABLES
    ]


def association_tables() -> list:
    """The Core `Table` objects that carry `account_id` without a declarative class."""
    from mihomes.models import Base

    return [Base.metadata.tables[name] for name in sorted(ASSOCIATION_TABLES)]


def check_registry() -> list[str]:
    """Return a list of discrepancies between the registry and the live metadata.

    Empty means consistent. Called by A1's test, and worth calling from anything
    that generates DDL: a table present in metadata but absent from both
    `TENANT_TABLES` and `GLOBAL_TABLES` is unclassified, which means nobody decided
    whether it is tenant-scoped — the state that produces a leak.
    """
    from mihomes.models import Base

    problems: list[str] = []
    live = {
        name for name in Base.metadata.tables
        if not name.startswith("alembic_version") and name not in TEST_ONLY_TABLES
    }

    unclassified = live - TENANT_TABLES - GLOBAL_TABLES
    for name in sorted(unclassified):
        problems.append(
            f"{name}: in metadata but classified neither tenant-owned nor global. "
            "Add it to TENANT_TABLES or GLOBAL_TABLES in mihomes/tenancy/registry.py "
            "— an unclassified table gets no RLS policy and no isolation test."
        )

    for name in sorted(TENANT_TABLES - live):
        problems.append(f"{name}: in TENANT_TABLES but not in metadata (stale entry?)")

    for name in sorted(GLOBAL_TABLES - live):
        problems.append(f"{name}: in GLOBAL_TABLES but not in metadata (stale entry?)")

    # Every tenant table must actually carry the column, however it got there.
    for name in sorted(TENANT_TABLES & live):
        if "account_id" not in Base.metadata.tables[name].columns:
            problems.append(
                f"{name}: registered tenant-owned but has no account_id column"
            )

    return problems
