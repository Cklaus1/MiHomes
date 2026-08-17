"""G16 · §6 Step 16 — import a pre-SPEC-002 SQLite database into a tenant account.

This is what makes an existing single-user install reachable again: the old schema has integer
primary keys, no `account_id` and no `accounts` table, so the multitenant ORM cannot read it at
all (launch gate S6).

**The ordering is the load-bearing part, and it is not stylistic (A20).** Object storage is not
transactional with Postgres, so the two cannot be committed together. The order is:

    1. build the id remap
    2. upload every file          <- not transactional
    3. VERIFY every object exists and its size matches
    4. only then commit the database transaction, carrying the rewritten keys

A failure at any point therefore leaves **orphaned objects (garbage), never dangling references
(corruption)**. The reverse order — commit rows, then upload — is prohibited: a crash between the
two leaves rows pointing at files that do not exist, and nothing in the database says so. Garbage
is recoverable by a sweep; corruption is found by a user opening a document that is not there.

**Three properties of real source data that shaped this, all measured rather than assumed** (from
the author's own 1,823-row install):

*Dangling polymorphic references are normal and must be preserved.* 118 of 505 `audit_log` rows
reference entities that no longer exist — unsurprising, since an audit log **records deletions**.
`entity_id` is NOT NULL, so they cannot be nulled. The remap therefore mints a UUID for **any**
`(table, old_id)` pair on first sight, whether or not that row exists, so a reference stays
consistently dangling and every audit row about deleted task 47 still shares one id. One
mechanism, not two.

*Dangling real foreign keys cannot always be imported, and the rule is per column.* 14 rows point
at missing parents. Whether that row survives depends on the **target** column's nullability, not
on a blanket policy:

    insurance_policies.property_id  nullable  -> imported with a NULL parent (2 rows saved)
    spaces.property_id              NOT NULL  -> skipped, and the skip cascades
    assets.property_id              NOT NULL  -> skipped
    vendor_ratings.vendor_id        NOT NULL  -> skipped

*A skip cascades and must be counted.* Orphaning 9 spaces also orphans whatever lived in them.
The closure is computed **before** anything is written and reported per table with a reason,
because a skip reported as a count is a decision the operator can act on, while a skip discovered
later as a row-count mismatch is silent data loss.

**Imports into an empty account only.** Re-running against an account that already holds data
would either duplicate everything or trip `UNIQUE (account_id, slug)` partway through, leaving the
half-imported account the spec's verify clause rules out. Refusing is simpler than making 1,823
inserts idempotent, and it fails before writing rather than during.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine

from mihomes.ids import new_id

__all__ = [
    "ImportError_",
    "ImportPlan",
    "ImportReport",
    "FileMover",
    "FilesystemMover",
    "import_sqlite",
    "plan_import",
    "polymorphic_pairs",
]


class ImportError_(RuntimeError):
    """Import refused or failed. Named with a trailing underscore to avoid shadowing builtins."""


# `entity_type` -> source table, for the polymorphic columns that carry no foreign key.
#
# Hand-written because **no authoritative mapping exists to derive it from**: the three
# polymorphic tables use three inconsistent vocabularies (`audit_log` says `work_order`, `notes`
# says `workorder`, `documents` says `ha_entity` for a table that never existed). That is the same
# finding that made a drift-guard trigger impossible for these tables in G4. Both spellings are
# accepted here, deliberately, because both occur in real data.
ENTITY_TABLE: dict[str, str] = {
    "property": "properties",
    "space": "spaces",
    "zone": "zones",
    "staff": "staff",
    "vendor": "vendors",
    "task": "tasks",
    "issue": "issues",
    "asset": "assets",
    "work_order": "work_orders",
    "workorder": "work_orders",
    "contract": "contracts",
    "event": "events",
    "guest": "guests",
    "document": "documents",
    "note": "notes",
    "template": "templates",
    "transaction": "transactions",
    "appointment": "appointments",
    "budget": "budgets",
    "insurance": "insurance_policies",
    "recurring_expense": "recurring_expenses",
    "consumable": "consumables",
    "book": "books",
    "vendor_rating": "vendor_ratings",
    "event_guest": "event_guests",
}


# Legacy JSON id-lists that a later migration normalised into an association table.
#
# **This is not tidiness — omitting it silently destroys data.** The author's database predates
# the M14 normalisation for vendors: all 59 vendors carry a non-empty `property_ids` JSON blob and
# the source has **no `vendor_properties` table at all**. Treating `property_ids` as merely "a
# source column with no target column" would have dropped every vendor-to-property association,
# and the only sign would have been one line reading `dropped: property_ids` in the summary.
#
# Found by investigating that line rather than accepting it. `staff` needed no entry — the same
# survey confirmed the source already has its normalised `staff_properties` table with 21 rows.
#
# {(source_table, json_column): (assoc_table, own_fk, other_fk, other_parent)}
LEGACY_ID_LISTS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("vendors", "property_ids"): ("vendor_properties", "vendor_id", "property_id", "properties"),
}


class FileMover:
    """Moves a source file to its destination key, and can verify what it wrote.

    A narrow interface on purpose. G11's `StorageProvider` (S3) slots in behind it later without
    touching the importer: the ordering guarantee is about *sequence*, not about which backend
    holds the bytes, so it is testable — and tested — against any implementation.
    """

    def put(self, source: Path, key: str) -> int:  # pragma: no cover - interface
        """Write `source` at `key`; return the number of bytes written."""
        raise NotImplementedError

    def size(self, key: str) -> int | None:  # pragma: no cover - interface
        """Size of the object at `key`, or None if it does not exist."""
        raise NotImplementedError


@dataclass
class FilesystemMover(FileMover):
    """Development backend: copies under a root directory.

    Keys are tenant-prefixed by the caller, so two accounts importing a file with the same name
    cannot collide (A14's requirement, applied here to the import path).
    """

    root: Path

    def put(self, source: Path, key: str) -> int:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        return dest.stat().st_size

    def size(self, key: str) -> int | None:
        dest = self.root / key
        return dest.stat().st_size if dest.exists() else None


@dataclass
class ImportPlan:
    """What the import *would* do, computed before anything is written."""

    source: Path
    row_counts: dict[str, int] = field(default_factory=dict)
    # table -> [(reason, count)]
    skips: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    unparented: dict[str, int] = field(default_factory=dict)
    files: list[tuple[Path, str]] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    dropped_columns: dict[str, list[str]] = field(default_factory=dict)
    # table -> {old_id, ...}. Carried on the plan so the import uses the SAME set the plan
    # reported, rather than recomputing it with a second copy of the rule.
    skipped_ids: dict[str, set[int]] = field(default_factory=dict)
    # table -> ids that will exist after the import (live minus skipped). A foreign key is
    # unsatisfiable if its parent is not in here, whether it was skipped or never existed.
    available_ids: dict[str, set[int]] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())

    @property
    def total_skipped(self) -> int:
        return sum(n for entries in self.skips.values() for _, n in entries)

    def render(self) -> str:
        lines = [f"Import plan for {self.source}", ""]
        lines.append(f"  rows to import : {self.total_rows - self.total_skipped:,}")
        lines.append(f"  rows to skip   : {self.total_skipped:,}")
        lines.append(f"  files to move  : {len(self.files):,}")
        if self.missing_files:
            lines.append(
                f"  files MISSING  : {len(self.missing_files):,} "
                "(referenced by a row but not on disk)"
            )
        if self.skips:
            lines.append("")
            lines.append("  skipped rows, by table:")
            for table in sorted(self.skips):
                for reason, n in self.skips[table]:
                    lines.append(f"    {table:26} {n:>5,}  {reason}")
        if self.unparented:
            lines.append("")
            lines.append("  imported with a NULL parent (target column is nullable):")
            for table, n in sorted(self.unparented.items()):
                lines.append(f"    {table:26} {n:>5,}")
        if self.dropped_columns:
            lines.append("")
            lines.append("  source columns with no target column (dropped):")
            for table, cols in sorted(self.dropped_columns.items()):
                lines.append(f"    {table:26} {', '.join(cols)}")
        return "\n".join(lines)


@dataclass
class ImportReport:
    """What the import actually did."""

    plan: ImportPlan
    inserted: dict[str, int] = field(default_factory=dict)
    files_moved: int = 0
    account_id: uuid.UUID | None = None
    # "table.column" -> how many values were shortened to fit the target's length.
    truncations: dict[str, int] = field(default_factory=dict)
    # "table.column" -> association rows built from a legacy JSON id-list.
    expanded: dict[str, int] = field(default_factory=dict)

    @property
    def total_inserted(self) -> int:
        return sum(self.inserted.values())

    def render(self) -> str:
        lines = [f"Imported into account {self.account_id}", ""]
        for table in sorted(self.inserted):
            if self.inserted[table]:
                lines.append(f"    {table:26} {self.inserted[table]:>5,}")
        lines.append("")
        lines.append(f"  rows inserted : {self.total_inserted:,}")
        lines.append(f"  files moved   : {self.files_moved:,}")
        if self.expanded:
            lines.append("")
            lines.append("  legacy JSON id-lists expanded into association rows:")
            for where, n in sorted(self.expanded.items()):
                lines.append(f"    {where:34} {n:>5,}")
        if self.truncations:
            lines.append("")
            lines.append("  values shortened to fit the target column length:")
            for where, n in sorted(self.truncations.items()):
                lines.append(f"    {where:34} {n:>5,}")
        if self.plan.total_skipped:
            lines.append(f"  rows skipped  : {self.plan.total_skipped:,} (see the plan above)")
        return "\n".join(lines)


class _Remap:
    """Stable `(table, old_id)` -> UUID, minted lazily on first reference.

    Minting for ids that do **not** exist is the point, not an oversight: a polymorphic reference
    to a deleted row has to stay dangling, and two references to the same deleted row have to
    agree. One mechanism covers real remaps and dangling ones alike.
    """

    def __init__(self) -> None:
        self._map: dict[tuple[str, int], uuid.UUID] = {}

    def get(self, table: str, old_id: int) -> uuid.UUID:
        key = (table, int(old_id))
        if key not in self._map:
            self._map[key] = new_id()
        return self._map[key]

    def __len__(self) -> int:
        return len(self._map)


def _source_tables(con: sqlite3.Connection) -> list[str]:
    return sorted(
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'alembic_%'"
        )
    )


def polymorphic_pairs(engine: Engine) -> dict[str, list[tuple[str, str]]]:
    """`{table: [(id_column, type_column)]}` — discovered, never hardcoded.

    A polymorphic reference is a UUID column that is **not** a foreign key and has a sibling
    `*_type` column naming the table it points at. Deriving this from the target schema found
    **six** such pairs where a hand-written list would have had three:

        alerts.source_entity_id       <- would have been missed
        work_orders.source_id         <- would have been missed
        audit_log.entity_id
        documents.entity_id
        notes.entity_id
        tag_assignments.entity_id

    Both misses were found the same way: an import against real data failed with
    `column "source_entity_id" is of type uuid but expression is of type smallint`. This is the
    fifth time in this run that a derived list beat a hand-written one, so it is derived.

    It also corrects the G4 note, which said there were four polymorphic tables — the count of
    *tables carrying entity_type/entity_id* was four, but the count of polymorphic **columns** is
    six, and it is columns that need remapping.
    """
    from sqlalchemy.dialects.postgresql import UUID as PGUUID

    from mihomes.models import Base

    pairs: dict[str, list[tuple[str, str]]] = {}
    for name, table in Base.metadata.tables.items():
        fk_cols = {fk.parent.name for fk in table.foreign_keys}
        for col in table.columns:
            if col.name == "id" or col.name in fk_cols:
                continue
            if not isinstance(col.type, PGUUID) or not col.name.endswith("_id"):
                continue
            type_col = col.name[:-3] + "_type"
            if type_col in table.c:
                pairs.setdefault(name, []).append((col.name, type_col))
    return pairs


def _fit_length(
    table: str,
    col: str,
    value,
    truncations: dict[str, int],
    taken: dict[tuple[str, str], set[str]],
):
    """Fit a string into the target column's length, keeping unique columns unique.

    **SQLite does not enforce `VARCHAR(n)`; Postgres does.** Real data therefore contains strings
    the target rejects — a book in the author's library has a 108-character title used verbatim as
    its slug, against `VARCHAR(100)`:

        StringDataRightTruncation: value too long for type character varying(100)

    Truncating is the right call over refusing the row (losing a book) or aborting the import
    (losing everything), but a truncated **slug** could collide with another truncated slug and
    trip `UNIQUE (account_id, slug)` — turning a cosmetic fix into a failed import. So uniqueness
    is preserved explicitly: on collision, the tail is replaced with a short counter. Every
    truncation is counted and reported, because silently shortening someone's data is exactly the
    kind of thing that should appear in the import summary.
    """
    if not isinstance(value, str):
        return value
    limit = getattr(_target_type(table, col), "length", None)
    unique = _is_unique_ish(table, col)

    if limit is None or len(value) <= limit:
        if unique:
            taken.setdefault((table, col), set()).add(value)
        return value

    truncations[f"{table}.{col}"] = truncations.get(f"{table}.{col}", 0) + 1
    fitted = value[:limit]
    if not unique:
        return fitted

    seen = taken.setdefault((table, col), set())
    if fitted not in seen:
        seen.add(fitted)
        return fitted
    for n in range(2, 100_000):
        suffix = f"-{n}"
        candidate = fitted[: limit - len(suffix)] + suffix
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    raise ImportError_(f"could not build a unique {table}.{col} within {limit} characters")


def _is_unique_ish(table: str, col: str) -> bool:
    """Does `col` participate in a unique constraint or a slug-style uniqueness rule?"""
    from mihomes.models import Base

    target = Base.metadata.tables.get(table)
    if target is None:
        return False
    if target.c[col].unique:
        return True
    for con in target.constraints:
        cols = {c.name for c in getattr(con, "columns", [])}
        if type(con).__name__ == "UniqueConstraint" and col in cols:
            return True
    for idx in target.indexes:
        if idx.unique and col in {c.name for c in idx.columns}:
            return True
    return False


def _target_type(table: str, column: str):
    """The SQLAlchemy type of a target column, for coercion."""
    from mihomes.models import Base

    return Base.metadata.tables[table].c[column].type


def _coerce(value, target_type):
    """Convert a SQLite value to what the Postgres column expects.

    SQLite is dynamically typed and stores much of this as text or integers; Postgres is not, and
    rejects the mismatch outright rather than guessing. The cases that actually occur in real data,
    each found by an import failing on it:

        occupied     BOOLEAN   <- SQLite integer 0/1     "is of type boolean but expression is of
                                                          type smallint"
        created_at   TIMESTAMP <- SQLite text            "is of type timestamp ... but expression
                                                          is of type text"
        features     JSON      <- SQLite text
        due_date     DATE      <- SQLite text

    Driven by the **target** column's Python type rather than by a per-column list, so a column
    added later is coerced without anyone remembering to add it.
    """
    if value is None:
        return None
    try:
        py = target_type.python_type
    except NotImplementedError:  # e.g. custom types with no declared python_type
        return value

    if py is bool and not isinstance(value, bool):
        # SQLite has no boolean: 0/1, and occasionally 'true'/'false' text.
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "t", "yes")
        return bool(value)
    if py is datetime and isinstance(value, str):
        return _parse_datetime(value)
    if py is date and isinstance(value, str):
        parsed = _parse_datetime(value)
        return parsed.date() if parsed else None
    if py in (dict, list) and isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    if py is Decimal and isinstance(value, (int, float)):
        return Decimal(str(value))
    return value


def _parse_datetime(raw: str):
    """Parse the datetime spellings SQLite produces.

    `fromisoformat` handles most of them, including the space-separated form SQLite writes for
    `DateTime` columns. The `Z` suffix needs rewriting for Python < 3.11 compatibility and is
    cheap to keep.
    """
    text_value = raw.strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text_value, fmt)
            except ValueError:
                continue
    return None


def _has_id(con: sqlite3.Connection, table: str) -> bool:
    """Does `table` have a surrogate `id`? Association and natural-key tables do not."""
    return any(r[1] == "id" for r in con.execute(f"PRAGMA table_info({table})"))


def _foreign_keys(con: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """`[(column, parent_table)]` for `table`, taken from the **TARGET** schema.

    **The source is not the authority here, and assuming it was cost a real bug.** The old SQLite
    schema declares `transactions.work_order_id` as a bare `INTEGER` with no `FOREIGN KEY` clause,
    so `PRAGMA foreign_key_list` never mentions it — while the target has a genuine FK to
    `work_orders.id`. The importer happily copied the raw integer `1` into a UUID column and
    Postgres rejected it with `cannot cast type smallint to uuid`.

    The target is where the constraints that must be satisfied actually live, so the target defines
    what needs remapping. Source-only columns are ignored anyway (they have no target column), so
    reading from the target loses nothing.

    This is the fifth time in this run that a derived authority beat a declared one — after the
    tenancy registry, the drift-guard link list, the polymorphic column pairs, and the SlugMixin
    table count. `con` is kept in the signature so callers stay unchanged and so a future
    source-side check has somewhere to go.
    """
    from mihomes.models import Base

    target = Base.metadata.tables.get(table)
    if target is None:
        return []
    pairs: list[tuple[str, str]] = []
    for fk in target.foreign_keys:
        parent = fk.column.table.name
        if parent == "accounts":
            continue  # supplied directly, never remapped from the source
        pairs.append((fk.parent.name, parent))
    return sorted(set(pairs))


def _topological_order(tables: list[str], fks: dict[str, list[tuple[str, str]]]) -> list[str]:
    """Parents before children, so a foreign key never points at an unwritten row.

    Derived from the source's own foreign keys rather than hardcoded: a hand-maintained order
    silently rots the moment a table is added, and the failure mode is an FK violation halfway
    through an import.
    """
    ordered: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(
            t
            for t in remaining
            # A self-reference cannot be satisfied by ordering; those rows are written with the
            # remap already populated, so ignore them here.
            if all(p in ordered or p == t or p not in remaining for _, p in fks.get(t, []))
        )
        if not ready:
            # A genuine cycle. Emit the rest in a stable order rather than looping forever; the
            # insert will fail loudly if the cycle really is unsatisfiable.
            ordered.extend(sorted(remaining))
            break
        ordered.extend(ready)
        remaining -= set(ready)
    return ordered


def _compute_skips(
    con: sqlite3.Connection,
    tables: list[str],
    fks: dict[str, list[tuple[str, str]]],
    target: dict[str, dict],
) -> tuple[dict[str, set[int]], dict[str, dict[str, int]], dict[str, int], dict[str, set[int]]]:
    """The one definition of "which rows cannot be imported".

    Returns `(skipped_ids, reasons, unparented_counts, available_ids)`.

    `available_ids` is the set that survives per table — `live - skipped`. The import needs it,
    not just the skip set: a foreign key can be unsatisfiable because its parent was **skipped**
    *or* because that parent **never existed in the source at all**. Treating only the first case
    produced a `ForeignKeyViolation` on a nullable column whose parent id (99) was simply absent.
    Two ways to be unavailable, one predicate.

    **One function, used by both the plan and the import.** The first version of this file computed
    the closure twice — once for the human-readable plan and once to get the ids — and that is the
    duplication shape that has already cost this run four times (`_UNMANAGED_TABLES`, the four
    hand-rolled TestClient fixtures, 21 unguarded `session.get()` calls, three `entity_type`
    vocabularies). Two subtly different rules here means an import that drops rows the plan never
    mentioned, which is silent data loss.

    Transitive by construction: a row whose required parent is skipped is itself skipped, iterated
    to a fixpoint, because the answer is not knowable one row at a time.
    """
    # Not every table has a surrogate `id`. Measured on the real source: `staff_properties` is a
    # pure association table (staff_id, property_id) and `configurations` has a natural key
    # (key, value). Assuming `id` existed raised `no such column: id` on the real database —
    # the same Core-`Table`-has-no-class blind spot that has now appeared four times.
    keyed = [t for t in tables if _has_id(con, t)]
    live: dict[str, set[int]] = {
        t: {r[0] for r in con.execute(f'SELECT id FROM "{t}"')} for t in keyed
    }
    live.update({t: set() for t in tables if t not in keyed})
    skipped: dict[str, set[int]] = {t: set() for t in tables}
    reasons: dict[str, dict[str, int]] = {t: {} for t in tables}
    unparented: dict[str, int] = {}

    # (table, col, parent, row_id, parent_id) for every FK-bearing row, read once.
    edges: list[tuple[str, str, str, int, int]] = []
    for t in tables:
        for col, parent in fks.get(t, []):
            if parent not in tables or col not in target.get(t, {}):
                continue
            if t not in keyed:
                # A keyless row cannot be individually skipped, so it is filtered at insert time
                # by whether its parents survived (see `_rewrite_row`).
                continue
            for row_id, parent_id in con.execute(
                f'SELECT id, "{col}" FROM "{t}" WHERE "{col}" IS NOT NULL'
            ):
                edges.append((t, col, parent, row_id, parent_id))

    changed = True
    while changed:
        changed = False
        for t, col, parent, row_id, parent_id in edges:
            if row_id in skipped[t]:
                continue
            parent_missing = parent_id not in live[parent]
            parent_skipped = parent_id in skipped[parent]
            if not (parent_missing or parent_skipped):
                continue
            if target[t][col]["nullable"]:
                continue  # keep the row, drop the link; counted separately below
            skipped[t].add(row_id)
            reason = (
                f"orphaned {col} (parent row missing)"
                if parent_missing
                else f"{parent} row was skipped"
            )
            reasons[t][reason] = reasons[t].get(reason, 0) + 1
            changed = True

    # Counted after the fixpoint so a row that is skipped for another reason is not also
    # reported as unparented.
    for t, col, parent, row_id, parent_id in edges:
        if row_id in skipped[t] or not target[t][col]["nullable"]:
            continue
        if parent_id not in live[parent] or parent_id in skipped[parent]:
            unparented[t] = unparented.get(t, 0) + 1

    available = {t: live[t] - skipped[t] for t in tables}
    return skipped, reasons, unparented, available


def plan_import(source: Path, engine: Engine, *, media_root: Path | None = None) -> ImportPlan:
    """Work out what the import would do, without writing anything."""
    if not source.exists():
        raise ImportError_(f"Source database not found: {source}")

    plan = ImportPlan(source=source)
    target = {t: {c["name"]: c for c in inspect(engine).get_columns(t)}
              for t in inspect(engine).get_table_names()}

    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = [t for t in _source_tables(con) if t in target]
        fks = {t: _foreign_keys(con, t) for t in tables}

        for t in tables:
            plan.row_counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            src_cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            migrated = {c for (tt, c) in LEGACY_ID_LISTS if tt == t}
            dropped = sorted(src_cols - set(target[t]) - migrated)
            if dropped:
                plan.dropped_columns[t] = dropped

        skipped, reasons, unparented, available = _compute_skips(con, tables, fks, target)
        plan.skipped_ids = skipped
        plan.available_ids = available
        plan.unparented = unparented
        for t in tables:
            if reasons[t]:
                plan.skips[t] = sorted(reasons[t].items(), key=lambda kv: -kv[1])

        # --- files ------------------------------------------------------------------
        if "documents" in tables and plan.row_counts.get("documents"):
            for row in con.execute("SELECT id, file_path FROM documents"):
                raw = row["file_path"]
                if not raw:
                    continue
                candidate = Path(raw)
                if not candidate.is_absolute() and media_root:
                    candidate = media_root / raw.lstrip("/\\")
                if candidate.exists():
                    plan.files.append((candidate, candidate.name))
                else:
                    plan.missing_files.append(raw)
    finally:
        con.close()

    return plan


def import_sqlite(
    source: Path,
    engine: Engine,
    account_id: uuid.UUID,
    *,
    mover: FileMover | None = None,
    media_root: Path | None = None,
    dry_run: bool = False,
    plan: ImportPlan | None = None,
) -> ImportReport:
    """Import `source` into `account_id`. See the module docstring for the ordering contract."""
    plan = plan or plan_import(source, engine, media_root=media_root)
    report = ImportReport(plan=plan, account_id=account_id)

    _require_empty_account(engine, account_id)

    if dry_run:
        return report

    remap = _Remap()

    # ---- STEP 2/3: files first, then verify. Before any row is written. --------------
    if plan.files:
        if mover is None:
            raise ImportError_(
                f"{len(plan.files)} file(s) to move but no FileMover was supplied. Refusing: "
                "committing rows that reference unmoved files is the corruption this ordering "
                "exists to prevent."
            )
        written: dict[str, int] = {}
        for path, name in plan.files:
            # Tenant-prefixed key so two accounts importing the same filename cannot collide.
            key = f"{account_id}/documents/{name}"
            written[key] = mover.put(path, key)

        for key, expected in written.items():
            actual = mover.size(key)
            if actual is None:
                raise ImportError_(
                    f"Verification failed: {key} was uploaded but does not exist. No rows were "
                    "written, so nothing references it."
                )
            if actual != expected:
                raise ImportError_(
                    f"Verification failed: {key} is {actual} bytes, expected {expected}. No rows "
                    "were written."
                )
        report.files_moved = len(written)

    # ---- STEP 4: one transaction for every row. --------------------------------------
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        target_names = set(inspect(engine).get_table_names())
        tables = [t for t in _source_tables(con) if t in target_names]
        fks = {t: dict(_foreign_keys(con, t)) for t in tables}
        order = _topological_order(tables, {t: _foreign_keys(con, t) for t in tables})
        target = {t: {c["name"]: c for c in inspect(engine).get_columns(t)} for t in tables}
        skipped = plan.skipped_ids
        available = plan.available_ids
        poly = polymorphic_pairs(engine)
        truncations: dict[str, int] = {}
        taken: dict[tuple[str, str], set[str]] = {}

        from mihomes.models import Base

        with engine.begin() as conn:
            for table in order:
                if table not in target:
                    continue
                inserted = 0
                for row in con.execute(f"SELECT * FROM {table}"):
                    src = dict(row)
                    if src.get("id") in skipped.get(table, set()):
                        continue
                    values = _rewrite_row(
                        table, src, target[table], fks[table], remap, account_id, available,
                        truncations, taken, poly.get(table, []),
                    )
                    if values is None:
                        continue
                    # A Core insert, not raw `text()`. The statement carries the column types,
                    # so psycopg adapts UUID, JSON, boolean and timestamp values correctly. With
                    # raw text() the type information is gone and a JSON column fails with
                    # "cannot adapt type 'dict'" — the wrong layer for this job.
                    conn.execute(Base.metadata.tables[table].insert().values(**values))
                    inserted += 1
                report.inserted[table] = inserted

                # Expand any legacy JSON id-list this table carries into its association table,
                # inside the SAME transaction: a half-written association is exactly the partial
                # state this ordering exists to prevent.
                for (src_table, json_col), spec in LEGACY_ID_LISTS.items():
                    if src_table != table:
                        continue
                    assoc, own_fk, other_fk, other_parent = spec
                    # Checked against the TARGET database, not `target` — that dict holds only
                    # tables present in *both* schemas, and an association table the source never
                    # had is precisely the case this expansion exists for. Getting this wrong made
                    # the expansion a silent no-op: vendors imported, links did not.
                    if assoc not in target_names:
                        continue
                    src_cols = {r[1] for r in con.execute(f"PRAGMA table_info({src_table})")}
                    if json_col not in src_cols:
                        continue
                    made = 0
                    for row in con.execute(f'SELECT id, "{json_col}" FROM "{src_table}"'):
                        owner_old_id = row[0]
                        if owner_old_id in skipped.get(src_table, set()):
                            continue
                        raw = row[1]
                        if not raw:
                            continue
                        try:
                            ids = json.loads(raw) if isinstance(raw, str) else raw
                        except (TypeError, ValueError):
                            continue
                        for other_old_id in ids or []:
                            if other_old_id not in available.get(other_parent, set()):
                                continue  # parent gone: the link cannot be represented
                            conn.execute(
                                Base.metadata.tables[assoc].insert().values(
                                    **{
                                        own_fk: remap.get(src_table, owner_old_id),
                                        other_fk: remap.get(other_parent, other_old_id),
                                        "account_id": account_id,
                                    }
                                )
                            )
                            made += 1
                    if made:
                        report.inserted[assoc] = report.inserted.get(assoc, 0) + made
                        report.expanded[f"{src_table}.{json_col}"] = made
        report.truncations = truncations
    finally:
        con.close()

    return report


def _rewrite_row(
    table: str,
    src: dict,
    target_cols: dict,
    fks: dict[str, str],
    remap: _Remap,
    account_id: uuid.UUID,
    available: dict[str, set[int]],
    truncations: dict[str, int],
    taken: dict[tuple[str, str], set[str]],
    poly: list[tuple[str, str]],
) -> dict | None:
    """Rewrite one source row for the target schema, or None if it cannot be imported."""
    out: dict = {}

    for col, value in src.items():
        if col not in target_cols:
            continue  # source column with no target column; reported in the plan
        if col == "id":
            out["id"] = remap.get(table, value)
            continue
        if value is None:
            spec = target_cols[col]
            if not spec["nullable"] and spec.get("default") is not None:
                # Omit the column entirely so the server default fires. Passing NULL explicitly
                # would violate NOT NULL; the old schema was laxer than the new one in a few
                # places (e.g. `audit_log.timestamp`), and a NULL there is missing data rather
                # than meaningful data.
                continue
            out[col] = None
            continue
        if col in fks:
            parent = fks[col]
            if parent in available and value not in available[parent]:
                # Parent skipped, or never present in the source. Same consequence either way.
                if target_cols[col]["nullable"]:
                    out[col] = None
                else:
                    return None
                continue
            out[col] = remap.get(parent, value)
            continue
        poly_type = next((tc for ic, tc in poly if ic == col), None)
        if poly_type is not None:
            kind = src.get(poly_type)
            parent = ENTITY_TABLE.get(kind) if kind else None
            # An unknown or absent type is preserved rather than dropped: the row is history, and
            # a UUID that points nowhere is exactly as true as the integer that pointed nowhere.
            out[col] = remap.get(parent or f"__unmapped__{kind}", value)
            continue
        coerced = _coerce(value, _target_type(table, col))
        out[col] = _fit_length(table, col, coerced, truncations, taken)

    if "account_id" in target_cols:
        out["account_id"] = account_id
    return out


def _require_empty_account(engine: Engine, account_id: uuid.UUID) -> None:
    """Refuse to import into an account that already holds data.

    Checked before any write. Re-running would duplicate rows or trip `UNIQUE (account_id, slug)`
    partway through, and a half-imported account is precisely what the spec's verify clause rules
    out ("a simulated mid-import failure leaves no partial account").
    """
    from mihomes.models import Base
    from mihomes.tenancy.registry import TENANT_TABLES

    with engine.connect() as conn:
        present = set(inspect(engine).get_table_names())
        for table in sorted(TENANT_TABLES):
            if table not in present:
                continue
            # A Core `select`, not `text(f"...")`. G10's AST guard flagged the f-string version —
            # correctly, and on my own new code. The table names here come from the registry so
            # nothing was injectable, but that was exactly the reasoning G10 rejected: the point
            # is that raw SQL is invisible to the tenant filter, so the ORM layer is used wherever
            # a mapped table exists.
            target_table = Base.metadata.tables[table]
            n = conn.execute(
                select(func.count())
                .select_from(target_table)
                .where(target_table.c.account_id == account_id)
            ).scalar()
            if n:
                raise ImportError_(
                    f"Account {account_id} already has data ({n} row(s) in {table}). Import "
                    "targets an empty account only — re-importing would duplicate rows or fail "
                    "partway, leaving a half-imported account. Create a new account, or delete "
                    "this one's data first."
                )
