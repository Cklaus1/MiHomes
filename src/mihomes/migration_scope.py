"""Shared constants for the Phase 0 landing migration tree.

`alembic_landing/env.py` is an Alembic script, not an importable module, so a
constant both it and the tests need has to live in the package.

**Why the version table is named at all.** `alembic/` and `alembic_landing/` are
independent trees and both would otherwise default to `alembic_version`. Harmless
while the landing app has its own database — which SPEC-001 D1/D3 require — but if
the two were ever pointed at one database they would fight over a single version
row, each treating the other's revision as unknown. SPEC-002 does not anticipate a
second tree at all, so this guard is ours rather than the spec's.
"""

from __future__ import annotations

__all__ = ["IDENTITY_TABLES", "VERSION_TABLE"]

VERSION_TABLE = "alembic_version_landing"

# Tables that live on Base.metadata but are NOT in the legacy SQLite tree.
#
# The `alembic/` tree's autogenerate oracles compare the SQLite schema against
# Base.metadata, so anything on the metadata that SQLite does not have reads as
# drift. `waitlist` is excluded because alembic_landing/ owns it; these six are
# excluded because SPEC-002's Postgres baseline (Step 6) creates them and the
# legacy SQLite chain never will.
#
# This is legitimate drift, not a false positive — the tables really are absent
# from that schema. Once 0001_pg_baseline lands and the legacy revisions move to
# alembic/legacy_sqlite/, these oracles retire with the tree they check, and this
# set retires with them.
IDENTITY_TABLES = frozenset({
    "accounts",
    "users",
    "memberships",
    "membership_property_scopes",
    "invites",
    "sessions",
})
