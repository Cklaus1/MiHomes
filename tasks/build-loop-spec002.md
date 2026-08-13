# SPEC-002 Build Loop — Phase 1: Multitenant Foundation

> **Input spec:** `docs/specs/SPEC-002-phase1-multitenant-foundation.md` (695 lines, *Ready to
> build — **no open decisions***)
> **Conventions:** `tasks/build-loop-conventions.md` — all mechanisms (stop condition, poison
> ceiling, circuit breaker, artifact routing) are defined there and inherited here, **unchanged**.
> **Branch:** `worktree-spec-build-harness` → `origin/spec-build`. **Target ref:** HEAD.
> **Invocation:** `/loop tasks/build-loop-spec002.md`

**This is the load-bearing phase.** SPEC-003 through SPEC-008 each describe *this* spec's design
rather than code. SPEC-004 §0.1 names the consequence: *"divergence compounds — if SPEC-002 is
implemented differently than specified, every spec above it inherits the difference."* A shortcut
here is paid for five times.

**What Phase 1 delivers:** sign in with Google, and see only your own account's data. **A21 is the
definition of done** — the spec's words: *"If it is not green, Phase 1 is not finished regardless
of what else works."*

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, `TEST_DATABASE_URL` set | ✅ PostgreSQL **18.4**, trust auth on localhost |
| P2 | `psycopg[binary]` installed | ✅ 3.3.4 |
| P3 | CI with a Postgres service | ✅ **added in pre-flight `502d97a`** — A21/A23/Step 17 are now satisfiable as written |
| P4 | `boto3` for the S3 storage backend (Step 11) | ✅ **1.43.69** installed at run start (§3 lists it) |

Check P1 through Python, not `psql` — `psql` is not on PATH here (see conventions §3.2).

**Environment (§10).** Two distinct database URLs is deliberate: **the app must not connect as the
owner (N5), but Alembic must**, because migrations legitimately bypass RLS.

```
DATABASE_URL            postgres as the non-owner `app` role
MIGRATION_DATABASE_URL  owner role, Alembic only
TEST_DATABASE_URL       CI/local test Postgres
GOOGLE_CLIENT_ID/_SECRET, SESSION_SECRET, STORAGE_PROVIDER, S3_*
```

---

## 0.1 Stop condition — and condition C changes meaning mid-run

Per conventions §0, all five. **C is two different numbers in one run** — the pilot never had this:

| | Condition | For SPEC-002 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 below |
| **B** | every §6 step tasked **and** every §8 criterion gated | F.3a + F.3b |
| **C** | full suite green | **before G15: 1233 passed on SQLite.** **After G15: the *migrated* suite on Postgres.** |
| **D** | smoke green | `tests/integration/test_smoke_all_tools.py` (18) — **itself a G15 migration target** |
| **E** | every §8 criterion green by its own named test | all 23, F.2 |

**Why C splits.** Step 15 *replaces* `conftest.py`'s in-memory SQLite engine with a Postgres
fixture. Every test that took the old `session` fixture is affected, so the first full-suite run
after G15 will look like a mass regression if you compare it to 1233-on-SQLite. It is not. Record
the post-G15 number as the new baseline the moment G15 commits, and gate on *that* afterwards.

**Measured baseline — two platforms, two numbers, same collection:**

```
local (Windows, HEAD 502d97a)   1 failed, 1233 passed, 1 skipped   = 1235
CI    (Linux,  run 31512044524)  0 failed, 1235 passed, 0 skipped   = 1235
```

The local failure is the known Windows `os.kill` incompatibility in `test_backup.py`
(`os.kill(pid, 0)` — a POSIX signal-0 liveness probe Windows rejects with `WinError 87`), and the
local skip is the POSIX-only watchdog test. **Both pass on Linux**, so CI exercises two tests this
machine cannot, and the totals reconcile exactly at 1235.

**Gate on the platform you are running on.** Locally: 1233 passing plus the same one known
failure. In CI: 1235 passing, nothing failing, nothing skipped. **If a local run sees two
failures, or CI sees any, it is real.**

> **CI earned its place before SPEC-002 started.** Its first run caught a pre-existing packaging
> bug no local run could: `pytest-asyncio` was declared **nowhere** while
> `[tool.pytest.ini_options]` sets `asyncio_mode = "auto"` (without the plugin that setting
> silently does nothing and async tests never run), and `openai` was only an *optional* extra
> while seven tests import it unconditionally. `pip install -e ".[dev]"` on a clean machine could
> not run this suite; the local pass was an artifact of what happened to be installed. Fixed in
> `f515ec2`.

**Invoke pytest as `py -m pytest`**, never `python` (Store shim). Pass DB env inline — the worktree
guard rejects `export` chains.

### Two expected skips, declared by name — ✅ RETIRED at G6.3

**Both are gone, and the skip count went 5 → 3 as a result.** G6.3 deleted the two files along with
the revisions they checked, and `tests/integration/test_pg_baseline.py::test_baseline_matches_metadata`
replaces their oracle with a Postgres one that needs no carve-out — it asserts the same
"models match schema" property against the tree that actually runs, so it can be green rather than
skipped. The remaining 3 skips are the POSIX-only watchdog test and two platform skips, none of them
SPEC-002's.

The original declaration is kept below because it is the record of *why* a skip was tolerated for
eleven groups, and conventions §0 treats an undeclared skip as red.

Conventions §0 says **a skipped test is a red gate**, so any skip has to be declared here or the
F.2 walk should flag it. Exactly two were expected for the duration of this spec:

```
tests/integration/test_migration_reconciliation.py::test_g_r4d_autogenerate_empty
tests/integration/test_money_migration.py::test_autogenerate_empty_after_money
```

Both assert *"the SQLite schema matches `Base.metadata`"* — a premise **G2–G6 deliberately break**
across 37 tables: `TenantOwned`'s `account_id`, per-account `UNIQUE` constraints replacing global
ones, composite indexes, composite FKs, UUID PKs. Every group from here would trip them, so
maintaining an exclusion list for a tree that `0001_pg_baseline` archives to
`alembic/legacy_sqlite/` means growing that list for eleven groups and then deleting it.

**G6.3 owns deleting both tests** along with the revisions they check. Until then they are skipped
with a reason naming Step 6.

**This is a carve-out for two named nodes, not a relaxation.** No other skip is acceptable — in
particular a Postgres fixture skipping because `TEST_DATABASE_URL` is unset is still red, which is
the case the rule exists for. The behaviour these two files cover is not lost:
`test_money_cast_preserves_values` and `test_money_round_trip` assert the money cast's *values*
rather than its schema shape and stay green, as do the four G-R4 gate tests either side of them.

> **The `alembic/env.py` exclusion also retired — at G6.3, on schedule.** `IDENTITY_TABLES` was
> excluded there so `alembic revision --autogenerate` would not propose creating six tables in the
> SQLite tree, and it was load-bearing right up until the baseline. Removing it was a *precondition*
> for G6.2, not a cleanup after: autogenerating with it still in place would have produced a
> baseline **silently missing all six identity tables**. The constant is deleted from
> `mihomes/migration_scope.py` rather than left unused.

---

## 0.2 Pre-flight re-verification — five spec claims are stale, two blocking

Conventions §3.1 halts on mismatch. **All five are already measured and recorded in
`opportunities.md`; they are restated here because tasks below depend on them.**

### The two blocking ones change what gets built

**A22 and Step 17's "three `ai/tools.py` call sites" have no target.** That file contains **zero**
`text(` calls — the hardening pass `9d6e02c` rewrote them onto the ORM. The tenancy concern
**moved, it did not vanish**: `services/archive.py:45,61` still interpolates table names into
`text()`, and `backup.py:203` runs `PRAGMA foreign_key_check`. **A22 is retargeted at
`archive.py`** (see G17). Do not fabricate a test against code that no longer exists.

**`staff_properties` and `vendor_properties` are invisible to the entire tenancy mechanism.**
Measured: **38 metadata tables, 36 mapped classes.** Both are Core `Table(...)` objects
(`models/staff.py:10`, `models/vendor.py:11`), so:

- `TenantOwned` is a `@declared_attr` mixin and **cannot** apply → no `account_id`
- §4.3 derives `TENANT_TABLES` from `TenantOwned.__subclasses__()` → **no RLS policy**
- A1 and A21 iterate that registry → **neither ever tests them**

A cross-tenant read/write surface with no filter and no backstop, **while A21 reports green.** The
registry must enumerate them explicitly (G2.5, G7.2).

### Corrected counts — every one understates the work

| Spec claim | Reality | Where it bites |
|---|---|---|
| "the existing **33** test files pass" | **95** | G15, A23 (~2.9×) |
| "**28 of 33** use the `session` fixture" | **43 of 95** | G15 — the keep-the-name argument gets *stronger* |
| "**36** domain tables" | **37** tenant-owned (38 − `waitlist`) | G2–G5, G7, A1, A21 |
| "**36** revisions" archived | **40** | G6 |
| "only **one** `__table_args__` … nearly greenfield" | **four** (`budget`, `event`, `note`, `tag`) | G3 — must *merge with*, not replace |
| "`SlugMixin` … **15** models" | **16** classes | G5 — 16 constraints |
| F4 cites `ha_entity.py:21` | **no such model** | G5 — that sub-task is a **no-op** |

Single head is `4db594964c82`. A naive regex reports two because `f1e2d3c4b5a6` has a tuple
`down_revision` — artifact, not a branch. Do not re-derive it.

---

## 1. Task DAG

Seventeen groups, one per §6 step (the spec: *"Each step is independently verifiable and
separately committable"*), plus G-Final.

### Execution order deviates from §6's numbering — G15 runs immediately after G2

**Measured, not predicted.** Applying `TenantOwned` in G2 makes `account_id` **NOT NULL on 40
tables**, and every existing test creates rows without one. The affected-area suite went
**187 failed / 156 errors**, all `NOT NULL constraint failed: <table>.account_id`. G15's
`conftest.py` fixture — a seeded account with the ContextVar bound — is what makes those inserts
valid again.

**This is not a re-plan; it is the DAG this file already declares.** G15's dep string is
`*dep: G9*`, not "all previous", and none of G15.1–G15.4's `*Verify:*` clauses need RLS (G7) or
the scoped session (G8) — verified. §6's numbering is a *listing* order, not a topological one.

**Actual order: G1 → G2 → G15 → G3 … G14 → G16 → G17 → G-Final.**

Why this rather than the alternatives:

- **Every group from G3 on keeps a meaningful outer-loop gate.** Leaving the suite red until §6's
  Step 15 would mean twelve groups where §1.2's collateral-damage check reports nothing — the
  mechanism that caught the `_UNMANAGED_TABLES` propagation bug in the pilot.
- **No third exclusion mechanism.** Making `account_id` nullable "for now" is precisely the
  failure `require_account()` and N2 warn about — a nullable tenant column invites `if account:`
  checks that silently skip scoping — and a stub default account would need unwinding in G6.

**A restart reads checkboxes, so this note is load-bearing:** a green G15 sitting between G2 and
G3 is intentional, and its G9 dependency was satisfied for the fixture portion. Any G15 sub-task
that turns out to need G9's GUC behaviour defers to after G9 and is marked here.

> Logged in `opportunities.md` as a spec defect: SPEC-002 §6 should either order Step 15 before
> Step 2, or state that the suite is expected red in between. As listed, the step order is not
> executable.

### Second order deviation — G6.2 moves ahead of G7–G14, and G3 ahead of G6.2

Same reasoning as the first, different cause. `db.init_db()` builds its schema by running
`alembic upgrade head`, and no existing revision knows about `account_id`, so **61 errors and 10 of
the 17 remaining failures are one missing artifact — G6.2's baseline — not sixty separate bugs.**
Leaving G6.2 in its §6 position means running G7 through G14 with a quarter of the integration
suite dark, which is exactly the collateral-damage blindness the first deviation was written to
avoid.

G3 still goes first: it writes composite indexes into `__table_args__`, and generating the baseline
before it buys a second migration for indexes already known about. **Order: G3 → G6.2 → G7 …**

### Run state at pause (after `G6.1b`)

```
full suite   1355 passed, 17 failed, 61 errors, 5 skipped
tests/unit   fully green
tests/web    fully green (32 passed)
```

**The new condition-C baseline is deliberately NOT recorded yet.** It moves again with G6.2, and a
baseline written mid-flight is the same defect already logged once in `lessons.md` (recording a
number from `pytest --co` instead of a real run). Record it when G6.2 commits and the migration-
backed tests are green or knowingly not.

Of the 17 failures: 10 are the G6.2 blocker (`test_dedup`, `test_offset_ack`), 1 is the known
Windows `os.kill` baseline failure (`test_backup`), and 6 are open —
`test_demo_boot` (2, `LookupError: current_account` — the demo-boot path needs an account context),
`test_csv_cmd` (2), `test_business_logic::test_delete_nonexistent_raises` (2).

> **Do not read "1355 passed" as validation of all of G6.1b.** A green suite proves the sites a test
> exercises, and the reason G6.1b was needed at all is that this layer is thinly covered. Verified by
> a failing-then-passing test: the three `test_form_validation` 422s, the two `staff.py`
> assignment-sync paths (`test_web_smoke::test_directory_edit_assigns_property` /
> `_unassigns_property`), and the PTO regex (`test_gateway_review_common`). **Changed by reading
> only, with no test asserting them:** `tasks.py:105-106`, `documents.py:82,107`,
> `calendar.py:227-264`, `issues.py:75-115`, `work_orders.py:72,120` — roughly 19 of the coercion
> sites. They are mechanical and individually simple, but "mechanical" is what the G6.1 false-green
> was too. Worth a coverage pass before G-Final claims A-anything about the web layer.

### [x] G1 — identity models — *no deps* — *16 tests; A2 green; metadata 38 -> 44 tables*

- [x] G1.1 · §6 Step 1 · — · `models/account.py`, `user.py` (**GLOBAL**), `membership.py`, `invite.py`, `session.py` (**GLOBAL**), `membership_property_scope` per §4.2 · verify: models import
- [x] G1.2 · §6 Step 1 · A2 · the **one-active-owner partial index** — `Index(..., unique=True, postgresql_where=text("role = 'owner' AND status = 'active'"))` (D4) · verify: `tests/unit/test_membership.py::test_one_owner_partial_index`

> `User` and `Session` are **global** — no `account_id`, no RLS. Both are read *before* account
> context exists; a tenant policy on `sessions` returns zero rows and **locks every user out**.
> `Account` has **no `owner_user_id`** — ownership lives in `memberships`, enforced by that partial
> index. N7: do **not** add `'invited'` to `memberships.status`; an invitee has no `user_id` yet.

### [x] G2 — `TenantOwned` on 37 tables — *dep: G1*

- [ ] G2.1 · §6 Step 2 · — · the `TenantOwned` mixin (§4.1) in `models/__init__.py` — `account_id` PGUUID, `ForeignKey("accounts.id", ondelete="CASCADE")`, `nullable=False`, `index=True` · verify: mixin imports
- [ ] G2.2 · §6 Step 2 · — · declare it on all **37** tenant-owned models (not 36) · verify: `tests/unit/test_tenancy_registry.py`
- [ ] G2.3 · §6 Step 2 · — · remove `unique=True` from `SlugMixin.slug` (`models/__init__.py:32`) — under multitenancy it makes the *second* account to create a "main-house" property fail · verify: registry test
- [ ] G2.4 · §6 Step 2 · A1 · the registry itself — the same one A21 uses, so a new model is covered automatically · verify: `tests/unit/test_tenancy_registry.py::test_all_models_tenant_owned`
- [ ] G2.5 · §6 Step 2 · A1 · **`account_id` on `staff_properties` and `vendor_properties` by hand**, and the registry must **enumerate them** — a mixin cannot reach a Core `Table` · verify: `test_tenancy_registry.py` asserts **all 37** tenant tables *positively*, association tables included

> **G2–G5 are the risk concentration, and N1 says so:** *"Under-scoping this is the most likely way
> the phase slips."* The mixin buys the *column* once; Steps 3, 4, 5 and 7 each require a **separate
> pass over the same 37 tables** — roughly 37×4 table-touches, not four tasks. That is why these
> groups carry sub-tasks per pass rather than one per step.
>
> **G2.5 exists because the spec's own mechanism cannot see two of its tables.** If the registry is
> `TenantOwned.__subclasses__()` alone, A1 and A21 both pass while `staff_properties` and
> `vendor_properties` are readable and writable across tenants. Assert the registry **positively**
> against a hardcoded list of 37 — a sampled or derived list rots, which is the A11 lesson from the
> pilot.

### [x] G3 — composite indexes lead with `account_id` — *dep: G2* — *43 non-leading → 17, all deferred by name; 4 gate tests*

- [x] G3.1 · §6 Step 3 · — · per-table index audit; every composite index leads with `account_id` · verify: `tests/unit/test_tenant_indexes.py::test_composite_indexes_lead_with_account_id`
- [x] G3.2 · §6 Step 3 · — · **merge with the four existing `__table_args__`** — `budget.py`, `event.py`, `note.py`, `tag.py` · verify: `test_tenant_indexes.py::test_preexisting_table_args_survived_the_merge` asserts each surviving constraint **by name**

> The spec says *"only one model declares `__table_args__` (`note.py:11`), so this is nearly
> greenfield."* **There are four.** Replacing rather than merging silently drops existing
> constraints. Confirmed and located: `budget.py:24` (`uq_budget_property_category_period`),
> `event.py:62` (`uq_event_guest`), `note.py:15` (`ix_note_entity`), `tag.py:24`
> (`uq_tag_assignment`) — the last two on `TagAssignment`/`Note`, not `Tag`/the note table's owner,
> which is why a line-number search for them misleads. Three more exist but are SPEC-002's own
> (`membership.py` ×2, `configuration.py`) and are already account-aware.

**The audit, and why the number went 43 → 17 rather than 43 → 0.** Measured: 40 tenant tables, **84
indexes, 43 of which did not lead with `account_id`.** The discriminating question is not "is it
composite" but **what the query knows at lookup time**, which sorts them into four buckets:

- **Converted (the actual G3 work, 26 indexes).** FK and time/filter columns queried within an
  account — `transactions.{property_id,vendor_id,work_order_id,appointment_id}`,
  `tasks.{due_date,zone_id,category}`, `audit_log.{timestamp,entity_type+entity_id}`,
  `issues.{reported_by_id,resolved_by_id}`, and so on. The single-column index is **replaced**, not
  supplemented: the composite serves every lookup the single did, and keeping both taxes every
  insert.
- **Deferred to G5 (16).** The 15 `SlugMixin` slug indexes plus `tags.name`. G5's
  `UNIQUE (account_id, slug)` produces a leading index by itself, so converting here writes them
  twice. **G5 must delete its entries from `EXPECTED_NON_LEADING`**; a companion test
  (`test_every_declared_exception_still_exists`) fails if a listed index disappears, so the
  allow-list cannot outlive its reason.
- **Permanent exception (1).** `invites.token_hash` stays globally unique and non-composite. An
  invite is accepted by presenting the token **before** the recipient belongs to any account, so the
  lookup cannot supply one — and `(account_id, token_hash)` would let two accounts mint the same
  hash. A comment at the column says so, because this is precisely what a later
  "every index leads with `account_id`" sweep would break.
- **Out of scope.** `users`, `sessions`, `accounts`, `waitlist` are GLOBAL.

> **The allow-list is exact, not a superset** — 17 declared, 17 actual, verified. An eighteenth
> non-leading index fails the gate rather than being absorbed.

> **Recorded for G7/G12, cheap now and expensive later:** RLS on `invites` blocks the token lookup
> above outright, since the accepting request has no `app.current_account` yet. Either `invites`
> needs a policy that permits the pre-tenant read, or the accept flow runs outside RLS. Same shape
> as the `src/web/` finding — the thing that bites later is trivially recordable at the moment you
> notice it.

**On the spec's `EXPLAIN` verify clause.** Not used as the gate, deliberately. A sequential scan is
the *correct* plan on a table with a handful of rows, so an `EXPLAIN` assertion fails or passes for
reasons unrelated to whether the index is right, and seeding each of 40 tables enough to move the
planner would buy a slow test that still only checks the cost model. The gate asserts index
existence and **column order** in metadata — the property Step 3 actually asks for — plus
`test_indexes_exist_in_postgres`, which confirms `create_all` really created them and closes the gap
a metadata-only assertion leaves open.

### [x] G4 — child-table drift guard — *dep: G2* — *52 links guarded by trigger; 4 tests*

- [x] G4.1 · §6 Step 4 · — · **real-FK children** — 52 links across 27 child tables, guarded by a trigger rather than a composite FK (evidence below) · verify: `tests/integration/test_drift_guard.py::test_child_account_mismatch_rejected`
- [x] G4.2 · §6 Step 4 · A12 · **polymorphic, no FK** — app-only enforcement, with the residual exposure named · verify: `test_drift_guard.py::test_polymorphic_tables_are_documented_as_uncovered`

**Third order/mechanism deviation: a trigger, not a composite FK — and this one was measured
before it was chosen.** Step 4's *guarantee* is "a child row whose `account_id` differs from its
parent's is rejected by the database"; the composite FK is its suggested *mechanism*. The mechanism
was built and probed against real Postgres, and it does not survive contact with the ORM:

1. A composite FK **alongside** the existing single-column FK gives two FK paths between the same
   pair of tables → `AmbiguousForeignKeysError` on `configure_mappers()`. Measured.
2. **Replacing** the single-column FK does configure — and joins on both columns, correctly — but it
   makes `account_id` a write target for every relationship into the child. SQLAlchemy warns that
   `Transaction.vendor` and `Transaction.property` both copy `<parent>.account_id` into
   `transactions.account_id`. Silencing that needs `overlaps=` on **53** relationships, and the
   write ambiguity is real rather than cosmetic.

What the composite FK *did* prove, in a scratch schema, is worth keeping: four composite FKs sharing
one `account_id` column create fine, a NULL optional parent is accepted (MATCH SIMPLE), and a
cross-tenant parent raises `ForeignKeyViolation`. The trigger reproduces all three deliberately —
including the `IS NULL` early return, which is why `test_null_optional_parent_accepted` exists as a
named test rather than a comment.

**Three things the trigger buys beyond avoiding the ORM fight:**
- **18 fewer indexes.** A composite FK needs `UNIQUE (account_id, id)` on all 18 referenced parents,
  since Postgres requires a unique constraint on the referenced column list. The trigger needs none.
  *(This supersedes the earlier note that G5 must not remove one of two overlapping unique
  constraints — there is now only `(account_id, slug)` to add.)*
- **It reaches the two Core `Table` association tables.** `staff_properties` / `vendor_properties`
  take constraints as positional `Table(...)` args, not `__table_args__`, so they needed a different
  edit shape for a composite FK. DDL does not care.
- **One definition.** A single parameterised PL/pgSQL function, with the 52 links derived from
  metadata rather than hand-listed — so a new FK is guarded the moment it is declared, and
  `test_trigger_present_on_every_guarded_child` fails on a 28th child rather than silently skipping.

> **The DDL is attached to `Base.metadata`, not just to the migration.** `create_all` does not create
> triggers, so a guard living only in G6.2 would be absent from every test database and the drift
> test would pass against an unguarded schema — the false-green shape this run has already produced
> twice. **G6.2 must import `DRIFT_GUARD_FUNCTION` / `trigger_ddl_statements()` from
> `mihomes/tenancy/drift_guard.py`, not copy the SQL.**

> **psycopg3 gotcha, recorded because it is invisible until it fires:** psycopg scans statement text
> for client-side placeholders and rejects anything that is not `%s`/`%b`/`%t`, so a PL/pgSQL body
> using `format('… %I …')` fails *before Postgres sees it* — "only '%s', '%b', '%t' are allowed as
> placeholders, got '%I'". The function therefore contains **no `%` at all**: `to_jsonb(NEW) ->> col`
> for the dynamic field read, `quote_ident` concatenation for the lookup, and
> `RAISE … USING MESSAGE =` instead of a `%`-formatted message. Escaping as `%%` would have worked
> only depending on whether the driver was handed an empty parameter tuple or `None`.

**G4.2 — the polymorphic four get app-only enforcement, and here is the evidence for that choice.**
The spec allows "a trigger, or application-only enforcement **and say so**". A trigger needs an
`entity_type` → table mapping in SQL, and **no authoritative mapping exists to derive one from** —
the three tables use three inconsistent vocabularies. Measured:

| table | distinct `entity_type` values | notable |
|---|---|---|
| `audit_log` | 22 | says `"work_order"` |
| `documents` | 9 | includes **`"ha_entity"`, which is not a table at all** |
| `notes` | 13 (`ENTITY_TYPE_MAP`) | says `"workorder"` — no underscore |

`notes` and `audit_log` spell the same concept two different ways, and `documents` points at a
table the pre-flight already established does not exist. A partial mapping would either reject
legitimate rows or silently skip them, which is worse than not claiming the guarantee.

> **Residual exposure, stated rather than implied:** these four get `account_id` from the G8.3
> `before_flush` listener, so an ORM insert lands in the writer's tenant. Unguarded: (a) a raw-SQL
> insert, and (b) an ORM insert whose `entity_id` was read from another tenant's row — the child is
> stamped with the *writer's* account while pointing at a foreign parent. **A21 does not cover this.**

> **Spec finding (the fourth), and note its direction.** F5 names **five** polymorphic tables; there
> are **four**. `alerts` is not polymorphic — it has a real `property_id` FK and is therefore fully
> drift-guarded. Unlike the other three findings this one means the spec *understated* existing
> protection rather than overstating it; a reader skimming the defect log should not assume every
> discrepancy widened scope. `tag_assignments` sits in **both** buckets: its `tag_id` side is
> guarded, its `entity_id` side cannot be.

### [x] G5 — unique constraints — *dep: G2* — *15 slug tables + tags.name; 4 tests*

- [x] G5.1 · §6 Step 5 · A3 · `UNIQUE (account_id, slug)` on each `SlugMixin` class · verify: `tests/integration/test_per_account_uniqueness.py::test_every_slug_table_has_the_constraint`
- [x] G5.2 · §6 Step 5 · A4 · `tag.name` per-account · verify: `test_per_account_uniqueness.py::test_two_accounts_can_reuse_a_slug_and_a_tag_name`
- [x] G5.3 · §6 Step 5 · — · **skip `task.py:90`** per F4 (now line 106). The `ha_entity` sub-task is a **NO-OP — that model does not exist** · verify: `test_task_schedule_task_id_stays_globally_unique` asserts the skip rather than trusting it

**The count is 15, and the earlier "not 15 — 16" correction was right about the number for the
wrong reason.** `event.py` does have two `SlugMixin` classes (`Event`, `Guest`), and the total is
still 15. The 16th is **`dummy`** — a test-only model that registers itself into `Base.registry` the
moment its module is imported, so the count reads 15 when the file runs alone and 16 under the full
suite. That is exactly how it was found: the test passed in isolation and failed in the full run.
`TEST_ONLY_TABLES` is now excluded explicitly.

**The schema was the easy half. The spec's verify clause failed on the *service* layer.** With
`UNIQUE (account_id, slug)` in place, the second account creating "Main House" still got
`main-house-2` — because `ensure_unique_slug()` searched the whole table with no account filter, and
`create_tag()`'s get-or-create matched on `name` alone. Both are now account-scoped:

- `ensure_unique_slug` de-duplicating against another tenant's row is a **cross-tenant read**, and a
  mild information leak on its own — the `-2` suffix tells you another tenant used that name.
- `create_tag` was worse in kind: an unscoped get-or-create **returns account A's Tag row to
  account B**, handing over a foreign primary key rather than merely failing to insert.

> **Both were fixed here rather than left to G8.1's ambient read filter, deliberately.** G8.1's
> `with_loader_criteria` would also catch them, but a function whose whole purpose is to enforce a
> uniqueness rule has to know that rule's scope — depending on an ambient filter means it breaks
> silently the first time a caller uses a path the filter does not reach (a bulk op, raw SQL, a
> `Query` built outside the session hook). Correctness at the point of use, backstop at the session.

> **This is a DAG dependency the spec does not record:** Step 5's verify clause is not satisfiable by
> schema changes alone. Any future spec step whose verification runs through a service getter has the
> same latent coupling to G8.1.

> **Known gap, checked explicitly because G5 could have caused it and did not.** `ensure_unique_slug`
> now calls `require_account()`, which raises `LookupError` with no context set — and measured,
> **neither `services/demo.py` nor `cli/init.py` establishes one**. That path was *already* broken
> before G5: `create_property` inserts a row whose `account_id` is NOT NULL, so G8.3's stamp listener
> raised the same `LookupError` at flush time. G5 moves the failure a few frames earlier without
> changing its class, and `test_demo_boot`'s two failures are in the known set both before and after
> (17 → 17). **The real gap: `mihomes init` and demo seeding cannot run under tenancy until
> something establishes a bootstrap account context.** That belongs to **G13** (CLI re-point) — noted
> here so it is not rediscovered as a G6.2 migration problem, which is what it will look like.

### [x] G6 — `0001_pg_baseline` — *dep: G3, G4, G5* — *43 tables, 52 triggers, 22 enums; 6 tests*

- [x] G6.1 · §6 Step 6 · — · **convert the 37 models' PKs to UUID** — D2 locks UUIDv7 app-side via `mihomes.ids.new_id()` (shipped by SPEC-001, reused verbatim, **no DB-side default**). Measured: 37 tables still have `Integer` autoincrement PKs and **no §6 step names this** · verify: every tenant model's pk is PGUUID with an app-side default
- [x] G6.1b · §6 Step 6 · — · **make the id-*consuming* layers agree with UUID PKs** · verify: `tests/web/` green (32 passed); `test_gateway_review_common` PTO assertion green

> **G6.1b exists because G6.1's gate was a false-green, and that is a harness finding, not just a
> bug.** G6.1's `*verify:*` clause asserted the shape of the models' primary keys, and it passed.
> Nothing asserted that the code *reading* those ids still worked, so condition E was satisfied by a
> criterion that had been proven only at one end. This is the F.3b failure mode — a criterion
> satisfied vacuously — occurring *inside* a single criterion rather than across §8. **When a task
> changes a type, its verify clause has to name a consumer of that type, not only its definition.**
>
> What was actually broken, all of it invisible to the unit suite because it lives in the web and
> gateway layers: 43 `int`-typed id parameters across 17 route modules (every affected form endpoint
> returned 422 to a real browser), 19 `int(...)` coercions, `staff.py`'s assignment sync comparing a
> set of strings against a set of `UUID`s so *every* edit re-assigned and re-removed everything, and
> the PTO approve-by-reply gateway parsing `APPROVE\s+(\d+)` — digit-only, so under UUID ids it
> stopped matching at all and the flow died silently.
>
> Two rules settled the per-site fix: surrogate keys with no user-facing identifier (`note_id`,
> `alert_id`, `appointment_id`, `event_id`, `entry_id`) became `UUID`, keeping the 422-on-garbage
> that `int` provided; identifiers a URL or form may carry as a slug became `str`, so
> `resolve_identifier` handles id-or-slug and raises `EntityNotFoundError` → 404. Optional filter
> params became `str | None` and compare via `str(p.id)` — a `str`/`UUID` comparison is silently
> always false, so an "all properties" dropdown would have quietly stopped filtering instead of
> erroring.

> **G6.1 is a hard prerequisite for creating any of the new tables, not just tidiness.** G1 already
> introduced the mismatch: `membership_property_scopes.property_id` is `PGUUID` with a
> `ForeignKey("properties.id")`, while `properties.id` is still `Integer`. Metadata tolerates that
> — measured — but `CREATE TABLE` does not. So the identity tables cannot be created against a
> real database until this conversion lands, which is why G6 depends on G3/G4/G5 rather than
> running early. The mismatch is intentional and recorded rather than worked around: writing the
> new models with integer FKs would mean converting them again in G6.
- [x] G6.2 · §6 Step 6 · — · the squashed baseline: identity + domain + the 5 new, Postgres-native · verify: `tests/integration/test_pg_baseline.py::test_upgrade_then_downgrade_is_clean`
- [x] G6.3 · §6 Step 6 · — · archive the **40** old revisions to `alembic/legacy_sqlite/` · verify: `test_pg_baseline.py::test_single_head_and_no_legacy_revisions`
- [x] G6.4 · §6 Step 6 · — · **`waitlist` is NOT in the baseline** · verify: `test_pg_baseline.py::test_waitlist_is_not_in_the_baseline` + `test_migration_waitlist.py::test_the_two_trees_do_not_overlap`

**Measured after `upgrade`:** 43 tables + `alembic_version`, 52 drift triggers, 22 enum types, the
guard function present, no `waitlist`. `alembic check` reports **"No new upgrade operations
detected"** — the baseline matches `Base.metadata` exactly, which is the empty-autogenerate oracle
the two deleted SQLite tests used to carry, now green on the tree that actually runs.

### ✗ CORRECTION — "the 61 errors are one missing artifact" was wrong

I wrote that in this file and in two commit messages, and the baseline proves it false. **The count
did not move: 61 errors before G6.2, 61 after.** They were **two stacked blockers**, and fixing the
first only exposed the second:

| | before G6.2 | after G6.2 |
|---|---|---|
| cause | `sqlite3.OperationalError: no such column: <table>.account_id` | `LookupError: <ContextVar 'current_account'>` |
| owner | G6.2 (schema) | **G13** (no account context in the CLI path) |

Verified, not assumed: all 61 errors across all five files (`test_cli.py` ×48,
`test_cli_bad_input.py` ×8, `test_cli_markup_injection.py` ×2, `test_dashboard.py` ×2,
`test_report_upcoming.py` ×1) are now `LookupError`. The schema blocker is genuinely gone — the
migration builds `account_id` on every table — but these tests still cannot pass until something
establishes a bootstrap account context, which is the gap already recorded under G5 and assigned to
G13. **The lesson: "N tests are blocked on X" is a hypothesis until X lands.** I stated it as fact
twice, and a reader planning around it would have expected 61 tests to go green here.

**What did move:** failures 17 → 17 with a different composition (10 of the old ones were
`test_dedup`/`test_offset_ack`, which now fail on the same `LookupError` rather than on the schema),
and **the two expected skips in §0.1 are gone** — retired exactly as predicted, taking the skip
count from 5 to 3.

### Four things the migration needed that autogenerate does not produce

1. **`DROP TYPE` for all 22 enums in `downgrade()`.** `op.drop_table` does not drop the Postgres
   enum type it created, so `upgrade → downgrade → upgrade` died on "type already exists". The
   spec's verify clause is exactly that cycle, so without this the gate fails — and a downgrade that
   leaves types behind *looks* clean, because every table is gone. Only re-upgrading catches it.
2. **An import for the custom `Money` type.** Autogenerate renders it fully qualified as
   `mihomes.type.money.Money()` and emits no import, so the generated file raised `NameError` on the
   first money column.
3. **The drift-guard DDL, imported not pasted** — `DRIFT_GUARD_FUNCTION` and
   `trigger_ddl_statements(Base.metadata)` come from `mihomes/tenancy/drift_guard.py`, so the
   migration and `create_all` cannot diverge. `test_drift_guard_triggers_created_by_the_migration`
   closes the gap that `test_drift_guard.py` leaves open: that file proves the guard exists in the
   *`create_all`-built* suite database and would stay green if the migration forgot it entirely.
4. **A dialect guard on the guard.** `CREATE OR REPLACE FUNCTION` is a syntax error on SQLite, and
   `install_drift_guard()` already skips non-Postgres — the migration now applies the same rule, so
   the two paths cannot differ by backend. **Consequence, stated plainly: a SQLite database built
   from this migration has no drift guard and no RLS.** SPEC-002 retires SQLite (RLS, the non-owner
   role, and transaction-local `set_config` all require Postgres); until G13 re-points
   `init_db()`'s default, those CLI tests exercise application logic against a schema with no tenant
   enforcement and **are not evidence for A21.**

> **`IDENTITY_TABLES` retired with the tree it protected.** It excluded the six identity tables from
> `alembic/env.py`'s autogenerate so the old oracles would not read them as drift. Leaving it in
> place would have generated a baseline **silently missing six tables** — its own comment predicted
> this retirement, and removing it (rather than leaving dead config) is the same call as the
> `src/web/` gitignore line.

> **Tests deleted: 10.** `test_migration_reconciliation.py` (7) and `test_money_migration.py` (2)
> replayed the archived chain, plus one migration-level test in `test_vendor_properties.py` written
> against integer PKs. Nothing lasting went with them — `tests/unit/test_money_type.py` covers the
> `Money` type itself, and `test_pg_baseline.py` (6 new tests) replaces their oracle with a stronger
> Postgres one. `alembic/legacy_sqlite/README.md` records the reasoning at the archive.

> **A test of mine polluted the suite, caught only by the full run.** `alembic.ini`'s
> `fileConfig()` defaults to `disable_existing_loggers=True`, so each `command.upgrade()` in
> `test_pg_baseline.py` silently switched off loggers configured earlier — three `test_email_service`
> tests failed in the full suite while passing in isolation. Fixed by clearing
> `cfg.config_file_name`. **A test that passes alone and fails in the suite is the suite telling you
> about shared state**, and this is the second time that signal paid out today (the first being
> `dummy` in the SlugMixin count).

> **Why `waitlist` is excluded — decided 2026-08-10.** D3 lists it as a global table, and SPEC-001
> built it in a **separate `alembic_landing/` tree** with its own database (D1: *"shares the stack
> and nothing else"*, D3: *"`waitlist` table only"*). SPEC-002 mentions `alembic_landing` zero
> times. The baseline covers the tenant/identity tables; `alembic_landing/` keeps owning
> `waitlist`, and it already sets an explicit `version_table` so the two trees cannot contend.
>
> **The old chain is not merely being replaced, it never worked on Postgres.** Measured: it dies at
> `e5f6a7b8c9d0` on a native-enum literal. So G6.2's `*Verify:*` is the **first** path proven to
> work, not a regression check — and `legacy_sqlite/` is correctly labelled "never run".

### [ ] G7 — `0002_rls` — *dep: G6*

- [ ] G7.1 · §6 Step 7 · A8 · **generate** the policies, do not hand-write 37 near-identical blocks (§4.3). `FORCE ROW LEVEL SECURITY` (owners bypass plain RLS); `current_setting('app.current_account', true)` so an unset GUC yields **zero rows, not an error** · verify: `tests/integration/test_rls.py::test_unset_guc_returns_empty`
- [ ] G7.2 · §6 Step 7 · A8 · `TENANT_TABLES` must include the **two association tables** — deriving it from `__subclasses__()` leaves them with no policy at all · verify: `test_rls.py` asserts a policy exists on all 37
- [ ] G7.3 · §6 Step 7 · A9 · `WITH CHECK` rejects an insert stamped with another account · verify: `tests/integration/test_rls.py::test_with_check_rejects_foreign_account`
- [ ] G7.4 · §6 Step 7 · A10 · the **one** bootstrap exception — `membership_self` on `memberships`, keyed on `app.current_user`, so the account picker works before account context · verify: `tests/integration/test_rls.py::test_membership_self_policy`

> The `(SELECT current_setting(...))` wrapper is load-bearing: it forces an InitPlan, evaluated
> once per query rather than once per row. And `memberships` is the **only** table that gets a
> user-keyed policy — §4.2 says keep it that way.

### [ ] G8 — scoped session — *dep: G7*

- [ ] G8.1 · §6 Step 8 · A5 · `do_orm_execute` filter via `with_loader_criteria`; **fail closed** — no context raises `LookupError` · verify: `tests/unit/test_scoped_session.py::test_fails_closed_without_context`
- [ ] G8.2 · §6 Step 8 · A6 · **N2: do not guard on `is_select` alone.** `with_loader_criteria` also applies to ORM UPDATE/DELETE, and a bulk-write leak is worse than a read leak · verify: `tests/unit/test_scoped_session.py::test_bulk_ops_scoped`
- [x] G8.3 · §6 Step 8 · A7 · `before_flush` stamps `account_id` on insert · verify: `tests/unit/test_scoped_session.py::test_insert_stamped`
- [ ] G8.4 · §6 Step 8 · — · `tenancy/context.py` — `account_context()`, `require_account()`. **Never returns None**: a nullable accessor invites `if account:` checks that silently skip scoping · verify: `test_scoped_session.py`

### [ ] G9 — connection hygiene — *dep: G8*

- [ ] G9.1 · §6 Step 9 · A11 · `after_begin` GUC (**transaction-local**, N3 — a session-level `SET` persists on a pooled connection and the next tenant inherits it), pool `checkin` `RESET`, `pool_pre_ping` · verify: `tests/integration/test_connection_hygiene.py::test_no_guc_leak_across_transactions`

> N3 is the subtlest rule in the spec: Fly's PgBouncer runs in **transaction** pooling mode, so a
> session-scoped GUC outlives the request that set it. Two sequential transactions on one pooled
> connection under different accounts must never see each other's rows.

### [ ] G10 — raw-SQL audit — *dep: G8*

- [ ] G10.1 · §6 Step 10 · — · **`services/archive.py:45,61`** — the real remaining interpolation. **NOT `ai/tools.py`: that file has zero `text(` calls; the hardening pass already fixed it** · verify: `tests/unit/test_no_raw_sql_interpolation.py`
- [ ] G10.2 · §6 Step 10 · A13 · the static guard must be **AST-based, not grep**. `grep 'text(f"'` matches `write_text(f"` and `save_document_text(f"` — two false positives that make the spec's verify clause unsatisfiable · verify: `tests/unit/test_no_raw_sql_interpolation.py::test_no_fstring_text_calls`
- [ ] G10.3 · §6 Step 10 · — · `backup.py:203` runs `PRAGMA foreign_key_check` — a SQLite assumption in the `text()` census · verify: covered by G14

### [ ] G11 — `StorageProvider` — *dep: G6* · *needs P4 (`boto3`)*

- [ ] G11.1 · §6 Step 11 · — · Protocol + exceptions + factory, S3 backend, filesystem dev backend · verify: `tests/unit/test_storage.py`
- [ ] G11.2 · §6 Step 11 · A14 · `Document.file_path` → an **opaque key**; keys are tenant-prefixed; presigned URLs only (**tenant files are never world-readable**) · verify: `tests/unit/test_storage.py::test_key_prefix_and_roundtrip`

> **N6: never a Fly volume.** Single-machine local NVMe silently caps the app at one machine *and*
> puts tenant files outside any backup.

### [ ] G12 — auth — *dep: G8*

- [ ] G12.1 · §6 Step 12 · A15 · Google OIDC + PKCE, `users` upsert on `sub`, server-side sessions · verify: `tests/integration/test_auth.py::test_signin_flow`, `::test_rejects_forged_token`
- [ ] G12.2 · §6 Step 12 · A16 · session cookie is httpOnly + Secure + SameSite=Lax · verify: `tests/integration/test_auth.py::test_cookie_flags`
- [ ] G12.3 · §6 Step 12 · A17 · revoking a membership denies access on the **next request** · verify: `tests/integration/test_auth.py::test_revocation_immediate`
- [ ] G12.4 · §6 Step 12 · — · CSRF (double-submit), `/signout`, sign-out-everywhere · verify: `test_auth.py`

> SPEC-001's OAuth stub is the reference for the *verification* half — real signature check, real
> `aud`/`iss`/`exp` validation (see `landing/oauth.py`). What Phase 1 adds is what Phase 0
> deliberately refused: a `users` row and a session. **Only the session store hashes the id** — the
> raw session id goes to the cookie and never to the database, same discipline as the confirm token.

### [ ] G13 — CLI re-point — *dep: G8*

- [ ] G13.1 · §6 Step 13 · — · `db.py` → Postgres; drop the SQLite PRAGMA hook; ops commands take `--account` · verify: `mihomes task list --account <slug>` returns only that account's tasks
- [ ] G13.2 · §6 Step 13 · — · **N9: `skip_tenant` is the `sudo` of this codebase.** Admin/ops only, greppable, code-reviewed · verify: a test enumerates every `skip_tenant` use site

### [ ] G14 — `backup.py` + `doctor` — *dep: G13*

- [ ] G14.1 · §6 Step 14 · — · **drop the `pg_dump` path** (D13) — managed Postgres owns DB backups and PITR; a second unmonitored backup system is worse than none because it invites false confidence · verify: no `pg_dump` in `backup.py`
- [ ] G14.2 · §6 Step 14 · — · keep and build the **media sync** — no database backup covers object storage. `mihomes backup` becomes media-only **and its docstring must say so** · verify: round-trips media to and from object storage
- [ ] G14.3 · §6 Step 14 · A18 · `doctor` drops its `DB_PATH`/`MEDIA_DIR` assumptions (which produce a false *"Database not found"* and **skip every later check**), keeps the ORM integrity checks, adds a stale-backup check against the RPO window · verify: `tests/integration/test_ops_commands.py::test_doctor_no_filesystem_assumptions`

> D14: **rehearse a restore before the first non-founder tenant.** Not automated, not optional — do
> it once by hand and write down how long it took. That number is the real RTO. An untested restore
> is not a backup.

### [ ] G15 — test-suite migration — *dep: G9* · *condition C changes here*

- [x] G15.1 · §6 Step 15 · — · replace `conftest.py`'s in-memory SQLite engine with a Postgres fixture (`TEST_DATABASE_URL`, skipping when unset) + `account_a` / `account_b` fixtures · verify: fixtures import
- [x] G15.2 · §6 Step 15 · A23 · **keep the `session` fixture's name and semantics** — it now yields an account-scoped session. **43 of 95 files use it** (not 28 of 33); renaming means touching 43 files · verify: full suite green
- [x] G15.3 · §6 Step 15 · — · **reconcile the second conftest** — `tests/web/conftest.py` also builds SQLite (`StaticPool`) and the spec's Fixtures paragraph does not contemplate it · verify: `tests/web/` green
- [ ] G15.4 · §6 Step 15 · — · docker-compose Postgres (D12). **`docker-compose.yml` already exists** and builds the Home Assistant demo stack — this is a **modify, not a create**; clobbering it breaks that setup · verify: compose config valid, HA services intact

> **Record the new baseline the moment this group commits.** A skip is a red gate (conventions §0):
> if `TEST_DATABASE_URL` is unset the Postgres fixture skips, the suite reads green, and the
> criteria that prove tenant isolation never ran.

### [ ] G16 — importer — *dep: G15*

- [ ] G16.1 · §6 Step 16 · A19 · `mihomes import <sqlite-path>` — read SQLite, build the **int→UUIDv7 remap** per source table · verify: `tests/integration/test_importer.py::test_roundtrip_counts_and_fks`
- [ ] G16.2 · §6 Step 16 · A20 · **the ordering is load-bearing:** upload all files → **verify every object exists and its size matches** → *then* commit the DB transaction. Failure leaves **orphaned objects (garbage), never dangling references (corruption)**. The reverse order is prohibited · verify: `tests/integration/test_importer.py::test_failure_leaves_nothing`

> Object writes are **not** transactional with Postgres, which is the whole reason for that order.
> This is where the data-preservation gate lives for this set (conventions §2) — not in the
> baseline, which runs against an empty database.

### [ ] G17 — the isolation test — *dep: all* · **the definition of done**

- [ ] G17.1 · §6 Step 17 · A21 · for **every** model in the registry: A can never read, update or delete B's rows — via ORM queries, ORM **bulk** `update()`/`delete()`, **and** raw `session.execute(text(...))` — and can never insert a row stamped with B's `account_id` (RLS `WITH CHECK` rejects it) · verify: `tests/integration/test_isolation.py::test_cross_tenant_denied_all_models`
- [ ] G17.2 · §6 Step 17 · A21 · the registry covers **all 37** tenant tables **including the two association tables** — assert positively against a hardcoded list, not a derived one · verify: `test_isolation.py` fails if a tenant table is missing from the registry
- [ ] G17.3 · §6 Step 17 · A22 · **RETARGETED: `services/archive.py`'s raw-SQL sites**, not `ai/tools.py`. The spec names three call sites in a file that now has zero `text(` calls; `archive.py:45,61` is where raw SQL defended by RLS alone actually remains · verify: `tests/integration/test_isolation.py::test_ai_tools_raw_sql_scoped` *(node id kept for traceability; docstring records the retarget)*

> **A21 is the phase's definition of done.** Treat a red A21 as a stop-the-run defect, not an
> ordinary failure — and check it by hand as well as by test. The pilot's A11 taught that a sampled
> assertion rots; this one must enumerate.

### [ ] G-Final — compound-stop verification — *dep: all*

- [ ] F.1 · full-suite green against the **post-G15** baseline (condition C)
- [ ] F.2 · all **23** §8 criteria green by the test named in their own row (condition E)
- [ ] F.3a · walk §6 top-to-bottom: every one of the **17** steps has a task (condition B, steps)
- [ ] F.3b · walk §8 top-to-bottom: every one of the **23** criteria has a gate (condition B, criteria)
- [ ] F.4 · **`skip_tenant` census** — every use site enumerated and justified (N9)
- [ ] F.5 · **SPEC-001 still holds** — the landing DB is exactly `{waitlist, alembic_version_landing}`, and every single-user route still 404s on the landing app (A11)
- [ ] F.6 · write `tasks/build-loop-spec002-report.md`

> **F.5 is new and generalizes the pilot's F.3b lesson one spec upward.** None of SPEC-002's 23
> criteria mention Phase 0, yet G2 touches 37 models and G15 rewrites `conftest.py` — either could
> break the landing app's isolation with **nothing noticing**. The reconciliation walk proves *this*
> spec's criteria are gated; it does not prove the previous spec's still pass.

---

## 2. Criteria → group map

F.3b reconciles against this. Every `A`-label from §8 appears exactly once.

| Criterion | Group | Criterion | Group |
|---|---|---|---|
| A1 | G2.4, G2.5 | A13 | G10.2 |
| A2 | G1.2 | A14 | G11.2 |
| A3 | G5.1 | A15 | G12.1 |
| A4 | G5.2 | A16 | G12.2 |
| A5 | G8.1 | A17 | G12.3 |
| A6 | G8.2 | A18 | G14.3 |
| A7 | G8.3 | A19 | G16.1 |
| A8 | G7.1, G7.2 | A20 | G16.2 |
| A9 | G7.3 | A21 | G17.1, G17.2 |
| A10 | G7.4 | A22 | G17.3 *(retargeted)* |
| A11 | G9.1 | A23 | G15.2 |
| A12 | G4.2 | | |

**23/23 bound.**

---

## 3. Spec-specific non-negotiables

Beyond conventions §6. These are §7's N-items — each names a failure that has already happened
somewhere, so none is a style preference.

- **N1 — `account_id` on 37 tables is not one task.** Three more passes follow. *"The most likely
  way the phase slips."*
- **N2 — do not guard the ORM filter on `is_select` alone.** A bulk-write leak is worse than a read
  leak.
- **N3 — no session-level `SET app.current_account`.** Pooled connections plus transaction-mode
  PgBouncer means the next tenant inherits it.
- **N4 — no `init_db()` or migrations on app startup.** `web/server.py:62,86` still does this
  (line numbers moved from the spec's `:39,63`). Multiple Fly machines race.
- **N5 — the app must never connect as table owner or a `BYPASSRLS` role.** Runtime is the
  non-owner `app`; migrations run as owner and bypass, which is correct and intended.
- **N6 — uploads never on a Fly volume.**
- **N7 — do not add `'invited'` to `memberships.status`.** An invitee has no `user_id`.
- **N8 — do not create a `homes` table.** A home is a `properties` row.
- **N9 — `skip_tenant` outside admin/ops tooling is prohibited.** *"It is the `sudo` of this
  codebase. Every use must be greppable and code-reviewed."* F.4 audits it.
- **Do not touch `alembic_landing/`.** SPEC-001 owns it, and F.5 checks it survived.
- **Do not "fix" the 565 pre-existing ruff findings.** Logged in `opportunities.md` as its own
  task; mixing it into the tenancy diff makes both unreviewable.

---

## 4. Open decisions

**None.** SPEC-002 is the only spec in the set with zero: its O1 (managed vs unmanaged Postgres)
and O2 (RPO/RTO) both closed 2026-07-31 → D13, D14. Every decision this phase depends on is
settled, so nothing here should poison on an unanswered question — if a task appears to need a
founder decision, re-read §1.1 before deferring it.

## 5. Known gaps

- **Two spec artifacts reference code that no longer exists** (A22, Step 17's "three call sites").
  Retargeted in G17.3 with the reasoning recorded, rather than fabricated or skipped.
- **D14's restore rehearsal is a human action.** G14 builds the checks; rehearsing the restore and
  writing down the real RTO is not automatable and belongs in the report as an unmet gate.
- **`boto3` (P4) must be installed before G11**, per §3's modified-files list.
