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

__all__ = ["VERSION_TABLE"]

VERSION_TABLE = "alembic_version_landing"
