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

## 0.0 UNMET LAUNCH GATES — things that are green *because they are switched off*

> Conventions §3.3: blocks-ship items are *"carried forward into the end-of-run report as an
> unmet launch gate — visible, not silently satisfied."* **I was not maintaining this register**,
> which is how G10 could disable a shipped feature and leave the suite greener than before. A
> passing suite does not mean the product works; it means the tests and the code agree. When a
> test is changed to assert a refusal, that agreement is about the refusal.

**None of these are counted in the 15 failures / 61 errors.** That is the point of listing them.

| # | What is off / degraded | Since | Green because | Owner |
|---|---|---|---|---|
| **S1** | **Archival does not work.** `run_archival()` raises `ArchivalUnavailableError`; `mihomes archive run` exits 1. The archive tables are not created by any migration and are not tenant-aware (INTEGER ids, no `account_id`). | G10 | the tests were rewritten to assert the refusal | **unowned — needs a retention decision** |
| ~~**S2**~~ | ~~A SQLite-built database has no RLS and no drift guard.~~ **Closed by refusal, not by making it work.** `init_db()` now raises `UnsupportedBackendError` on SQLite with an actionable message. Every DB-level control is Postgres-only, so a SQLite install would run and silently serve every tenant's rows — refusing is the only correct outcome. | G6.2 | — | **closed** |
| ~~**S3**~~ | ~~`mihomes init` cannot run.~~ **Closed for Postgres:** the account bootstrap in `init_db()` + the `--account` resolution mean the CLI works. 61 errors → 11. | G5 | — | **closed** |
| ~~**S6**~~ | ~~The local SQLite install is unreachable.~~ **Closed:** `mihomes import <path>` moves it into Postgres — 1,869 rows, 0 dangling FKs, verified on a copy of the real database. `mihomes-dev` still needs `DATABASE_URL`, which is now a setup step rather than a dead end. *(original text)* **The local SQLite install is unreachable, including `mihomes-dev` and demo mode.** `DATABASE_URL` must point at Postgres or the server refuses to start. The existing `~/.mihomes/db/mihomes.db` (37 tables, no `account_id`, no `accounts`) is **not readable by this schema** — measured. Data is untouched, just not loadable. | G13 | nothing tests the SQLite runtime, by design | **G16** (importer) |
| **S7** | **Demo mode is broken.** `load_demo_data` writes tenant-owned rows with no account context (`LookupError`), and the demo DB is SQLite, which is now refused. | G13 | `test_demo_boot`'s 2 failures are counted | **G16-adjacent — needs an account + Postgres demo DB** |
| ~~**S4**~~ | ~~The two association tables are protected by RLS alone, and nothing verifies the deployed role.~~ **Half closed:** the role is now verified at server startup (`verify_runtime_role`, N5). The association tables are still RLS-only, but RLS is no longer a promise. | G2.5 / G8 | — | **closed for the role; the RLS-only coverage is accepted** |
| **S5** | **Drift for the four polymorphic tables is app-only.** No `entity_type`→table mapping exists to build a trigger from. A raw-SQL insert, or an ORM insert whose `entity_id` came from a cross-tenant read, is unguarded. | G4.2 | the spec permits app-only enforcement *if stated*; **A21 does not cover it** | accepted, documented |

**S1 is the one at risk of slipping**, because unlike S2–S5 it has no group to land in. S2/S3 clear
when G13 runs; S4 becomes a deployment assertion; S5 is an accepted, recorded limitation. S1 needs
someone to decide whether Phase 1 ships without retention.

### Triage of the remaining red — every item has an owner

Done after G6 rather than deferred wholesale, because "17 failed / 61 errors" is not a state you can
act on and because one bucket turned out to be a live bug rather than scheduled work.

| count | what | owner | why it is not actionable now |
|---:|---|---|---|
| **61** | errors, all `LookupError: current_account` — `test_cli.py` ×48, `test_cli_bad_input` ×8, `test_cli_markup_injection` ×2, `test_dashboard` ×2, `test_report_upcoming` ×1 | **G13** | The CLI path establishes no account context. Fixing it *is* G13; doing it here would be G13 done early and out of dependency order (it depends on G8). |
| **12** | failures on the same `LookupError` — `test_dedup` ×6, `test_offset_ack` ×4, `test_demo_boot` ×2 | **G13** | Same root cause, reached through `db.init_db()` rather than the CLI runner. |
| **2** | `test_csv_cmd` — non-zero exit | **G13** | CLI-invoking; same context gap, different symptom. |
| ~~1~~ | ~~`test_backup::test_stale_pid_file_does_not_block_restore`~~ | **fixed at G14** | Was recorded as "platform, not actionable" — turned out to be a real bug reachable on Windows in production, not a test-environment artifact: `_live_service_pids()` only caught `ProcessLookupError`, which Windows never raises for a stale pid (`WinError 87` instead). Fixed by catching `PermissionError` first, then the broader `OSError`. `test_backup.py` itself is retired — its whole premise (SQLite backup/restore) is gone under G14's media-only rewrite — and the case lives on as `test_ops_commands.py::test_stale_pid_file_does_not_block_restore`. |
| ~~2~~ | ~~`test_business_logic::test_delete_nonexistent_raises` ×2~~ | **fixed here** | Not scheduled work — a real bug. See below. |

**The one that could not wait.** `delete_contract(session, 99999)` raised
`psycopg.errors.CannotCoerce: cannot cast type integer to uuid` — a `ProgrammingError`, which
surfaces to a user as a **500 instead of a 404**. Same class as the PTO bug fixed in G6.1b, in two
more places I had not swept. The sweep found **21 `session.get(Model, …)` call sites, exactly one of
which was guarded** (the PTO one). Fixed with a single `get_by_id()` helper in `services/slug.py`
rather than a third hand-written coercion, and applied to the 14 sites whose id arrives as a function
parameter — the surface where a caller can supply a non-UUID. Sites reading an id off an
already-loaded row are left alone: those values came from the database and are UUIDs by construction.

> **Why this matters beyond two tests:** the failing pair happened to have coverage. The other 12
> converted sites did not, and each was one non-UUID argument away from the same 500. The bug class
> was "any caller-supplied id that is not a UUID", and only a sweep bounds it — which is the same
> conclusion G6.1b reached about type changes needing a consumer-side gate.

### Current skip inventory — 3, all declared

Conventions §0 makes an **undeclared** skip a red gate, so the live set is listed here rather
than left to inference. Checked with `pytest -rs`, not assumed:

```
tests/unit/test_uuid_pks.py:41   configurations has a natural primary key — see NATURAL_KEY_TABLES
tests/unit/test_uuid_pks.py:85   configurations has a natural primary key — see NATURAL_KEY_TABLES
tests/unit/test_watchdog.py:20   POSIX-only crash
```

The two `test_uuid_pks` skips are **correct and permanent**, not deferrals: G6.1 gave
`configurations` a natural composite primary key `(account_id, key)`, so the two tests that assert
"every tenant table has a single UUID surrogate PK" have nothing to check there. They name
`NATURAL_KEY_TABLES` as the authority, so adding another natural-key table extends that set rather
than growing skip logic. `test_watchdog` is the pre-existing POSIX-only platform skip that passes on
Linux (CI).

**These two were undeclared until now** — they arrived with G6.1 and I did not add them to §0.1 at
the time, which is precisely the gap §0's rule exists to close.

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

### [x] G7 — `0002_rls` — *dep: G6* — *40 tables, FORCE, 11 tests*

- [x] G7.1 · §6 Step 7 · A8 · **generated** from the registry, not 40 hand-written blocks · verify: `tests/integration/test_rls.py::test_unset_guc_returns_empty`
- [x] G7.2 · §6 Step 7 · A8 · the two association tables carry policies · verify: `test_rls.py::test_every_registry_table_has_a_policy`
- [x] G7.3 · §6 Step 7 · A9 · `WITH CHECK` rejects a foreign-account insert · verify: `test_rls.py::test_with_check_rejects_foreign_account` (+ `..._allows_own_account`, so a WITH CHECK that rejected *everything* could not pass)
- [x] G7.4 · §6 Step 7 · A10 · `membership_self` on `memberships`, the only user-keyed policy · verify: `test_rls.py::test_membership_self_policy` (+ `..._is_the_only_user_keyed_policy`)

> The `(SELECT current_setting(...))` wrapper is load-bearing: it forces an InitPlan, evaluated
> once per query rather than once per row. And `memberships` is the **only** table that gets a
> user-keyed policy — §4.2 says keep it that way. Permissive policies **OR** together, so a
> user-keyed policy on any other table would punch a hole through that table's account scoping
> for every row that user can reach; there is now a test asserting there is exactly one.

### ⚠ The largest false-green surface in this run, and it was structural

**Superusers bypass RLS unconditionally — `FORCE ROW LEVEL SECURITY` binds the table *owner*,
not a superuser.** Measured on this cluster: as `postgres` with the GUC unset, a FORCE-protected
table returned **every row**.

The suite connects as `postgres`. So **all 1366 tests that existed before G7 ran with RLS inert**,
and RLS could have been entirely broken — or entirely absent — without one failure. Nothing about a
passing test says which role ran it.

Closed with a session-scoped **`app_engine`** fixture in `tests/conftest.py` on a dedicated
non-superuser role (`mihomes_test_app`), plus two tests that keep it honest:

- `test_app_role_is_not_a_superuser` — fails loudly if `app_engine` ever points at a superuser, so a
  later conftest edit cannot turn this whole file green and meaningless.
- `test_superuser_really_does_bypass_rls` — pins the asymmetry itself. It is a test of the
  *assumption*, not of our code: if a future Postgres made FORCE apply to superusers, this fails and
  the fixture's rationale becomes wrong rather than silently over-cautious.

> **G17/A21 MUST take `app_engine`.** A21 is the definition of done for Phase 1, and run as
> `postgres` it would demonstrate that the G8 ORM filter works **while reporting that RLS does** —
> a green light on the one criterion the spec says the phase hangs on. Written here now because from
> G17 it will look like a passing test.

> **Role creation is not in the migration.** A role is cluster-wide, not per-database, so
> `CREATE ROLE` in `0002_rls` would collide the second time it ran against another database in the
> same cluster. The fixture creates it idempotently; `0002_rls`'s docstring carries the production
> provisioning equivalent. `GRANT USAGE ON SCHEMA` is required and **not** implied by table grants —
> without it every table reports "relation does not exist" rather than "permission denied", which is
> a misleading way to find a missing grant.

### `NULLIF` in the predicate — a bug `missing_ok` does not cover

The policy is
`account_id = (SELECT NULLIF(current_setting('app.current_account', true), '')::uuid)`.
`missing_ok` (the `true`) only makes an **absent** GUC return `NULL`; a GUC **set to the empty
string** returns `''`, and `''::uuid` raises `invalid input syntax for type uuid: ""` — an error,
which is the single outcome Step 7 rules out.

**My first explanation of this was wrong and is worth recording as such.** I assumed that once any
`app.*` GUC was set, Postgres returned `''` for other unset members of the prefix. Measured: it does
not — `current_setting` returns `NULL` when unset, *even with another `app.*` GUC set*, and `''` only
when something explicitly assigns `''`. The unguarded cast was nonetheless observed failing with
exactly that error on `SELECT account_id FROM memberships`, so some path does supply an empty string.
`NULLIF` makes the predicate total over both spellings of "no account" rather than depending on which
arrives.

> **This is a live constraint on G9, not defensive habit.** G9 owns the pool-checkin `RESET` and the
> `after_begin` GUC. Clearing tenant context by assigning `''` is an entirely natural way to write a
> reset, and without `NULLIF` it would turn every subsequent query into a 500 instead of an empty
> result. G9 should clear with `RESET` / `set_config(..., NULL, ...)`;
> `test_empty_string_guc_is_treated_as_unset` pins the behaviour either way.

> **A10 is not fully satisfied by G7.** `membership_self` is keyed on a *second* GUC,
> `app.current_user`. The `current_user` ContextVar exists (G8.3) but **nothing sets the GUC** — that
> is G9's `after_begin`. `test_membership_self_policy` sets it directly, so it verifies the *policy*;
> until G9 wires it, the real account picker would return an empty list. Declared here rather than
> discovered then, the same way G5's verify clause was declared to depend on G8.1.

> **Third test-pollution bug of the run, and mine again.** RLS tests cannot roll back — the whole
> point is that a *second* connection reads what the first wrote, and an uncommitted row is invisible
> across connections. So these rows must be committed, and the first version leaked them into the
> session-scoped database. Because G8.1's read filter is still open, `list_properties()` is unscoped,
> and five unrelated tests in `test_web_smoke` / `test_form_validation` began failing in the full run
> while passing alone. Fixed with a `committed` fixture that records ids and deletes them in reverse
> dependency order.

### [x] G8 — scoped session — *dep: G7* — *11 tests; two §4.4 defects found*

- [x] G8.1 · §6 Step 8 · A5 · `do_orm_execute` filter via `with_loader_criteria`; **fail closed** · verify: `tests/unit/test_scoped_session.py::test_fails_closed_without_context`
- [x] G8.2 · §6 Step 8 · A6 · **N2** — the filter covers ORM UPDATE/DELETE, not just SELECT · verify: `test_scoped_session.py::test_bulk_ops_scoped`
- [x] G8.3 · §6 Step 8 · A7 · `before_flush` stamps `account_id` on insert · verify: `test_scoped_session.py::test_insert_stamped`
- [x] G8.4 · §6 Step 8 · — · `require_account()` **never returns None** · verify: `test_scoped_session.py::test_context_accessor_never_returns_none`

**§4.4's code snippet has two defects, and the first means it does not run at all.**

**(1) `current_account.get()` cannot be called inside the criteria lambda.** The spec writes
`lambda cls: cls.account_id == current_account.get()`. SQLAlchemy rejects it outright:

```
InvalidRequestError: Can't invoke Python callable get() inside of lambda expression argument
...; lambda SQL constructs should not invoke functions from closure variables to produce
literal values ... Call the function outside of the lambda and assign to a local variable
that is used in the lambda as a closure variable
```

So the fix — hoist into a local, let the lambda close over it — is the form SQLAlchemy itself
prescribes, not a precaution. **Confirmed by reverting to the spec's version and watching 4
tests fail**, which is also how `test_filter_is_not_cached_across_accounts` was shown to have
teeth rather than being decorative. That test runs one query shape under two accounts in
sequence, so if this ever became silent caching instead of a hard error it would still catch a
stale bound value.

**(2) The filter must not demand a tenant for statements involving no tenant entity.** §4.4
guards only on `is_select or is_update or is_delete`, so implemented literally it calls
`current_account.get()` for **every ORM statement in the process** and raises `LookupError`
whenever no account is bound. That is not a corner case:

- `users` and `sessions` are GLOBAL *precisely because* sign-in must read them **before** any
  account exists (D3). An unconditional check makes authentication impossible — the same
  bootstrap problem `membership_self` exists to solve, one layer up. **G12 would have hit
  this.**
- `waitlist` belongs to the standalone landing app, whose sessions are the same `Session`
  class this listener binds to. Implementing §4.4 literally broke **all 10 SPEC-001 landing
  tests** — which is how it was found.

Fixed by checking `state.all_mappers` and returning early when no `TenantOwned` entity is
involved. `all_mappers` covers top-level entities, so a join *from* a global table *to* a
tenant table still includes the tenant mapper and is still filtered —
`test_join_to_a_tenant_table_is_still_filtered` pins that, because otherwise "start the query
from a global entity" would be a bypass.

> **Third recurrence of the same blind spot: the ORM filter cannot reach the two Core
> association tables.** `with_loader_criteria` takes a mapped class, and `staff_properties` /
> `vendor_properties` have none. Their only protection is RLS — which is only real on a
> non-superuser connection. First the mixin could not reach them (G2.5), then `before_flush`
> could not (G8.3's column default), now the read filter cannot.
> `test_association_tables_are_not_covered_by_the_orm_filter` fails if either gains a mapped
> class, so the gap notes cannot quietly go stale.

> **The escape hatch is an execution option, `skip_tenant`**, for the paths that legitimately
> span tenants: Alembic, the Step 16 importer, admin tooling. Spelled per-call rather than as a
> context flag so every use is visible at the call site — a module-level "disable scoping"
> switch would be reachable from anywhere and invisible in review. Tested, so the paths that
> rely on it fail here rather than in production.

### [x] G9 — connection hygiene — *dep: G8* — *7 tests; A10 now closes end-to-end*

- [x] G9.1 · §6 Step 9 · A11 · transaction-local `after_begin` GUC (N3), `pool_pre_ping`, and — in place of the pool `checkin` `RESET` — an unconditional stamp · verify: `tests/integration/test_connection_hygiene.py::test_no_guc_leak_across_transactions`

> N3 is the subtlest rule in the spec: Fly's PgBouncer runs in **transaction** pooling mode, so a
> session-scoped GUC outlives the request that set it. Two sequential transactions on one pooled
> connection under different accounts must never see each other's rows.

**N3 measured rather than trusted.** With `pool_size=1, max_overflow=0` forcing every transaction
onto one physical connection:

```
session-level SET, second transaction on the same connection  -> sees 'bbbb'     LEAK
session-level SET, connection returned to the pool + reused   -> sees 'cccc'     LEAK
transaction-local set_config(..., true), next transaction      -> sees ''        cleared
```

**The pool pinning in those fixtures is the experiment, not scaffolding.** With a default pool the
second transaction may get a *different* connection and the test passes with the bug intact — a
version of `test_connection_hygiene.py` without `pool_size=1` would be exactly the false green N3
warns about.

### Step 9's pool `checkin` `RESET` was replaced, deliberately

*It does not work.* Executing SQL in the `checkin` event leaves an implicit transaction open on the
psycopg connection, and SQLAlchemy's own reset — which restores the isolation level, i.e. sets
`autocommit` — then fails with `can't change 'autocommit' now: connection in transaction status
INERROR`. Measured: it broke every fixture sharing the pool. `RESET` is also itself transactional, so
one issued inside a transaction that later rolls back is simply undone.

*Stamping every transaction is stronger anyway.* `after_begin` now sets both GUCs unconditionally —
the bound value, or **`NULL`** when nothing is bound. A transaction-local `set_config(guc, NULL, true)`
**overrides** a session-level value for the duration of the transaction (measured), so a stray `SET`
from a migration, a `psql` session on the same pool, or any future code cannot be observed by a
scoped query. The guarantee holds **at the point of use** instead of depending on the pool having
cleaned up — the same principle G5 applied to `ensure_unique_slug`.

### ✅ This closes the empty-string question G7 left open

G7 recorded that `NULLIF(..., '')` was needed in the RLS predicate but that *"some path supplies an
empty string"* and said so rather than inventing a mechanism. **Found here:** after a
transaction-local GUC's transaction ends, `current_setting('app.current_account', true)` returns
**`''`**, not `NULL`. Since every transaction after the first on a reused connection is in exactly
that state, the `NULLIF` is **required for correctness, not defensive** — without it the second
transaction on any pooled connection raises `invalid input syntax for type uuid: ""` instead of
returning zero rows. Two groups apart, and the second one explains the first.

### Both GUCs, and A10 finishes here

§4.4's snippet sets only `app.current_account`. §4.2's `membership_self` policy keys on
`app.current_user`, so setting only the account leaves A10's bootstrap policy permanently
unsatisfiable and the account picker returns an empty list — a failure that would present as
"sign-in works but you belong to no accounts". Both are stamped.

> **G7 declared A10 incomplete until this group; that claim is now discharged rather than left
> dangling.** `test_membership_self_works_through_the_real_app_path` binds the user via
> `account_context(..., user_id=...)`, lets `after_begin` set the GUC, and reads through a
> **non-superuser** connection with **no account bound** — the actual pre-picker state. Both
> conditions are required for the test to be able to fail at all.

### [x] G10 — raw-SQL audit — *dep: G8* — *zero `text(f"…")` in `src/`; archival gated*

- [x] G10.1 · §6 Step 10 · — · `services/archive.py` — the two interpolations are gone, rewritten onto the ORM · verify: `tests/unit/test_no_raw_sql_interpolation.py::test_no_fstring_text_calls`
- [x] G10.2 · §6 Step 10 · A13 · the static guard is **AST-based, not grep** · verify: `test_no_raw_sql_interpolation.py::test_the_guard_actually_detects_the_pattern`
- [x] G10.3 · §6 Step 10 · — · `backup.py:203`'s `PRAGMA foreign_key_check` — still a SQLite assumption, deferred to **G14** as planned

**The interpolation was the small half.** `text(f"SELECT COUNT(*) FROM {table_name}")` took its
table name from a hardcoded dict, so it was never injectable — but it was **raw SQL where the ORM
would do**, and a raw `text()` statement has no mappers, so the G8 filter cannot see it. Those
counts were therefore **cross-tenant totals**: one account's stats page reporting every account's
row count. Rewritten as `session.query(model).count()`, which both removes the interpolation and
makes the numbers mean what they always should have.

The AST guard is a `Call` whose callee is named exactly `text` with a `JoinedStr` first argument.
Grep cannot do this: `text(f"` matches `SESSION_FILE.write_text(f"…")` and
`save_document_text(f"…")`, two file writes with no SQL near them, so Step 10's clause is
permanently red for reasons unrelated to SQL. A companion test asserts the guard **catches**
`text(f"…")`, `sa.text(f"…")` and a nested form while **not** matching those two — a guard that
cannot fail is decoration.

### Archival is broken, and Step 6 revealed it rather than caused it

`audit_log_archive` / `ai_conversations_archive` were created by a raw-SQL revision in the SQLite
chain and were never on `Base.metadata`. **No migration in the current tree creates them.**
Measured: `run_archival()` raised `UndefinedTable: relation "audit_log_archive" does not exist`.

Stated carefully because the wrong framing invites the wrong fix: this is **not a regression from
G6.3**. `archive.py` depended on tables outside the managed metadata; the squash — or the first
fresh deploy — was always going to expose that. Reverting the squash would not make archival
correct.

**Not recreated, deliberately.** Their `id` columns are `INTEGER` while G6.1 made every source id
a UUIDv7, so `INSERT INTO audit_log_archive (id, …) SELECT id, …` cannot succeed even against the
original schema; and they have **no `account_id`**, so archived rows would have no tenant — not in
the registry, no RLS policy, no drift-guard link. A tenant-aware archive is retention's design
decision, not something a raw-SQL audit step should invent. `run_archival()` now raises
`ArchivalUnavailableError` with the reason, and `get_stats()` reports `already_archived: None`
rather than fabricating `0`.

> **`except Exception:` around a SQL statement is safe on SQLite and harmful on Postgres.** The old
> `try: … except Exception: archived = 0` was written so a missing table degraded gracefully. On
> Postgres the failed statement **aborts the transaction**, so the *next* unrelated query fails
> with `InFailedSqlTransaction` — an error pointing nowhere near the cause. Measured:
> `get_stats()` raised that instead of the `UndefinedTable` it had swallowed. Swept the codebase —
> one instance, now gone. To make a statement optional on Postgres, use a `SAVEPOINT` or do not
> issue it.

> **The tests were passing against a schema they invented themselves.** `test_archive.py` ran
> `CREATE TABLE IF NOT EXISTS audit_log_archive (id UUID, …)` in a helper and then asserted
> archival worked — so five tests were green while the feature failed on every real database. The
> fabricated table had **no `account_id`**, which means those tests asserted that tenant rows move
> into an untenanted table: the leak encoded as an expectation. Replaced with tests of the refusal,
> plus a structural check that the tables really are absent. The M8 boundary knowledge (bind the
> cutoff, never format it — `' ' < 'T'` made a string compare disagree with the ORM's strict `<`)
> is preserved in `_run_archival_unreachable`'s docstring beside the code that implements it.

> **Second time I asserted on source text and tripped over my own prose.** The first version of the
> "no test fabricates these tables" guard searched `.py` files for `CREATE TABLE …
> audit_log_archive` and matched **its own docstring**, exactly as G6.3's guard matched the
> baseline's comment about `waitlist`. Now a schema query. Recorded in `lessons.md` as a rule
> rather than an anecdote, since twice is a pattern.

### [x] G11 — `StorageProvider` — *dep: G6* — *33 tests; closed a live cross-tenant hole*

- [x] G11.1 · §6 Step 11 · — · Protocol + exceptions + factory, S3 backend, filesystem dev backend · verify: `tests/unit/test_storage.py`
- [x] G11.2 · §6 Step 11 · A14 · opaque tenant-prefixed keys; presigned URLs only · verify: `test_storage.py::test_key_prefix_and_roundtrip`

### ⚠ The hole G11 actually closed — it was live, not hypothetical

`web/app.py` mounted the uploads directory as static files:

```python
app.mount(UPLOADS_URL_PREFIX, SecureStaticFiles(directory=str(UPLOADS_DIR)))
```

**No authentication, no tenant check.** Any request that could reach the app could fetch **any**
tenant's document. The only obstacle was filename guessability, and that did not hold either:
uploads were `uuid4().hex` (fine), but generated reports were named
`f"{base_name}-{uuid4().hex[:8]}"` where `base_name` came from the report's **title** — 32 bits of
randomness attached to text the user can see. Obscurity was doing the work that authorisation
should have.

The mount is **removed**, not narrowed: a static mount has nowhere to put an authorisation check.
Documents are served by `documents_download.router`, which authorises against the account prefix in
the key **before reading a byte** — no database round trip, and nothing a storage backend's own path
handling can bypass.

> **The refusal is a 404, never a 403.** A 403 confirms the object exists and belongs to someone
> else, which turns "may I read this?" into "does this exist?" — enough to enumerate another tenant's
> documents. `test_refusal_is_404_not_403` asserts the foreign-key and never-existed cases are
> indistinguishable.

### Design decisions worth defending

- **Keys are `{account_id}/{category}/{uuid4().hex}{ext}`.** The account is *in the key*, which is
  what makes pre-storage authorisation possible. Only the extension survives from the client
  filename — a filename is **content** (`2026-divorce-settlement.pdf` in a log line is a
  disclosure), so the stem is discarded rather than sanitised.
- **Hostile filenames are dropped, not cleaned.** A sanitiser has to anticipate every escape;
  accepting only a short alphanumeric extension has no such failure mode.
- **The filesystem backend's `url()` returns `None` deliberately.** A URL from it would mean a static
  mount — the very hole removed. `None` forces the caller through the tenant-checked route.
- **No ACL is ever set on an S3 put.** An object written `public-read` is world-readable *forever*
  and no application check takes that back. The test asserts the request carries **no ACL at all**,
  so a future boto3 default cannot quietly become permissive.
- **Presigned expiry defaults to 15 minutes and is capped at one hour.** Anyone holding the URL can
  fetch the object until it expires; that is acceptable for a link handed to the requesting browser
  and is why it is not days. A caller asking for a week is capped rather than refused, so a mistake
  degrades instead of erroring.
- **The Protocol is deliberately narrow** — `test_provider_exposes_no_way_to_make_an_object_public`
  asserts there is no `make_public`/`set_acl`/`list_all`, because a method that can publish an object
  will eventually be called.
- **`STORAGE_PROVIDER=s3` without `S3_BUCKET` raises** rather than falling back to local disk: a
  hosted deployment quietly writing tenant documents to an ephemeral container filesystem loses them
  with no error, which is worse than not starting.

### Mutation-verified, per G17's lesson

Every control was broken and the matching assertion confirmed to fail:

```
tenant check removed          -> test_cannot_download_another_accounts_document   fails correctly
403 instead of 404            -> test_refusal_is_404_not_403                      fails correctly
public-read ACL on upload     -> test_no_public_acl_is_ever_set                   fails correctly
uploads static mount restored -> test_the_unauthenticated_uploads_mount_is_gone   fails correctly
presigned expiry uncapped     -> test_presigned_url_expires_and_is_capped         fails correctly
```

> **Removing the mount broke three writers, and finishing the job meant converting them.**
> `forms.save_document_upload`, `forms.save_document_text` and `assets._save_room_photo` all wrote
> straight to `UPLOADS_DIR` and returned `/uploads/<name>` — URLs that 404 once the mount is gone.
> All three now go through `_store_bytes`, the single place the web layer creates an object. Deleting
> the mount without this would have been a half-finished change that looked complete.

> **I wrote 8 test files into the author's real `~/.mihomes/media/objects`, and `lessons.md` had
> already warned about exactly this.** The fixture used `monkeypatch.setenv("MIHOMES_DIR", tmp)`,
> but `config.MEDIA_DIR` is computed from that variable **at config import time**, so it changed
> nothing. Removed the files (verified they were only fixtures — `%PDF-1.4 mine`, `not yours`,
> `exists`), then fixed the *cause*: `get_storage(override_root=...)` makes the root explicit, so a
> test cannot depend on getting a monkeypatch right. Reading a lesson is not the same as applying it.

> **N6: never a Fly volume.** Single-machine local NVMe silently caps the app at one machine *and*
> puts tenant files outside any backup.

### [x] G12 — auth — *dep: G8* — *23 tests, 8 controls mutation-verified, zero skips*

- [x] G12.1 · §6 Step 12 · A15 · Google OIDC + PKCE, `users` upsert on `sub`, server-side sessions · verify: `test_auth.py::test_signin_flow`, `::test_rejects_forged_token`
- [x] G12.2 · §6 Step 12 · A16 · session cookie is httpOnly + Secure + SameSite=Lax · verify: `test_auth.py::test_cookie_flags`, `::test_secure_flag_is_set_on_a_non_loopback_host`
- [x] G12.3 · §6 Step 12 · A17 · revoking a membership denies access on the **next request** · verify: `test_auth.py::test_revocation_immediate`
- [x] G12.4 · §6 Step 12 · — · CSRF (double-submit), `/signout`, `/signout-all` · verify: `test_auth.py::test_signout_requires_a_matching_csrf_token`

**The token verifier is SPEC-001's, reused rather than rewritten.** `landing/oauth.py` already does
JWKS fetch, signature check and `aud`/`iss`/`exp` validation with a 60-second skew, and it has tests.
A second verifier would mean two places to get `aud` wrong, and the one nobody reads is the one that
rots. G12 adds what Phase 0 refused: a `users` row, a session, and claim-level validation.

### Design decisions worth defending

- **Only the hash reaches the database.** The raw session id exists once, goes to the cookie, and is
  never stored. A backup or read-only leak yields nothing usable. **A bare SHA-256 is correct here**
  and a KDF would be wrong: the id is 256 bits of `secrets` output, so there is no dictionary to
  attack, and bcrypt would add latency to every authenticated request. Salting would break the
  lookup, since the point is to find a row *by* the hash.
- **`sub` is the identity, never the email.** An email can change hands; upserting on it would
  eventually hand one person's estate to whoever inherited their address. The email is refreshed on
  each sign-in for display only.
- **`email_verified` is enforced**, and an *absent* claim does not read as true — the usual shape of
  that bug.
- **The session id rotates on sign-in**, and the old row is deleted rather than superseded, so a
  planted (fixated) id authenticates nobody.
- **`Secure` is decided from the request's host, not a `DEBUG` flag.** A flag can be wrong in
  production; "this request arrived at localhost" cannot. Loopback drops `Secure` because a browser
  refuses such a cookie over plain http and dev could not sign in at all.
- **The CSRF cookie is deliberately *not* httpOnly** (the page must echo it into a form field) while
  the session cookie always is. The asymmetry is the point: the CSRF value carries no authority, the
  session carries all of it. Compared with `hmac.compare_digest`, and blanks never match.
- **`/signout` is a POST with CSRF.** A `GET /signout` can be triggered by any third-party page with
  an `<img src>`.

### A17 required a design decision the spec does not mention

`lookup_session` reads `Membership` with a **Core select, not the ORM**. `Membership` is
`TenantOwned`, so an ORM query invokes the G8 filter — which demands an account context. But
authentication is precisely the path that runs *before* any account exists: resolving the session is
**how** the account gets chosen. An ORM read would be circular, and reaching for `skip_tenant` would
put the codebase's `sudo` on the hot path of every request, which N9 forbids.

A Core select carries no mappers, so the filter correctly skips it — the same mechanism that lets
sign-in read GLOBAL `users`. The boundary is still enforced one layer down by RLS's `membership_self`
policy (A10, keyed on `app.current_user`), which exists for exactly this bootstrap case. **Two
mechanisms, each applying where the other cannot** — the same shape as G17's finding that each layer
must be verified where it is the only one present.

> **Revocation is re-checked every request and never cached in the session row.** A cached decision
> would leave a removed user with access until their session expired — 14 days, which is not
> "immediate" by any reading of A17. And a revoked membership **denies** rather than downgrading to
> "signed in, no account": downgrading would let them re-pick the very account they were removed from.

### Mutation-verified — and it caught a test with no teeth

```
membership check skipped      -> test_revocation_immediate                fails correctly
raw session id stored         -> test_only_the_hash_is_stored             fails correctly
session cookie not httpOnly   -> test_cookie_flags                       fails correctly
Secure never set              -> test_secure_flag_is_set_on_a_non_...     fails correctly
email_verified not enforced   -> test_unverified_email_is_refused         fails correctly
CSRF comparison always true   -> test_signout_requires_a_matching_csrf    fails correctly
OAuth state not checked       -> test_callback_rejects_a_mismatched_state fails correctly
no rotation on sign-in        -> test_signin_rotates_the_session_id       fails correctly *
```

*\* only after being rewritten.* The fixation test passed with rotation removed, for two reasons
worth keeping because both are easy to repeat: it planted a session for a **different user** than
sign-in resolves to (so a fresh id was minted either way), and it planted the row on a **different
connection** than the app uses (so the "planted session is gone" assertion checked a row that was
never visible). Fixed to use the same user and the app's own session factory.

> **A conditionally-skipped security assertion is a red gate, and I nearly shipped one.**
> `test_secure_flag_is_set_on_a_non_loopback_host` originally drove a request at
> `https://app.example.com`, which the Host guard rejects — so it ended in `pytest.skip` and would
> have skipped silently forever while nothing verified the production cookie was TLS-only. Replaced
> with a deterministic assertion against `_set_cookie` across four hosts. **Zero skips in this file.**

> **Third time: my own guard tripped on prose.** `test_skip_tenant_is_not_used_in_application_code`
> searched source *text*, so it failed the moment `auth/sessions.py` gained a docstring explaining
> why it deliberately avoids the escape hatch. Same mistake as the `waitlist` guard in G6.3 and the
> archive-table guard in G10. Now an **AST walk** for real usage — an import, a bare name, or the
> literal as an argument — ignoring docstrings. Verified both ways: it fires on a planted import and
> on a planted execution option, and does *not* fire on a docstring mention.

> SPEC-001's OAuth stub is the reference for the *verification* half — real signature check, real
> `aud`/`iss`/`exp` validation (see `landing/oauth.py`). What Phase 1 adds is what Phase 0
> deliberately refused: a `users` row and a session. **Only the session store hashes the id** — the
> raw session id goes to the cookie and never to the database, same discipline as the confirm token.

### [ ] G13 — CLI re-point — *dep: G8*

- [ ] G13.1 · §6 Step 13 · — · `db.py` → Postgres; drop the SQLite PRAGMA hook; ops commands take `--account` · verify: `mihomes task list --account <slug>` returns only that account's tasks
- [ ] G13.2 · §6 Step 13 · — · **N9: `skip_tenant` is the `sudo` of this codebase.** Admin/ops only, greppable, code-reviewed · verify: a test enumerates every `skip_tenant` use site

### [x] G14 — `backup.py` + `doctor` — *dep: G13* — *14 tests, zero skips*

- [x] G14.1 · §6 Step 14 · — · **drop the `pg_dump` path** (D13) — managed Postgres owns DB backups and PITR; a second unmonitored backup system is worse than none because it invites false confidence · verify: `test_backup_module_no_longer_imports_filesystem_paths` (AST check — no `DB_PATH` import survives, never had `pg_dump` to begin with)
- [x] G14.2 · §6 Step 14 · — · keep and build the **media sync** — no database backup covers object storage. `mihomes backup` becomes media-only **and its docstring must say so** · verify: `test_backup_restore_round_trips_through_storage`
- [x] G14.3 · §6 Step 14 · A18 · `doctor` drops its `DB_PATH`/`MEDIA_DIR` assumptions (which produce a false *"Database not found"* and **skip every later check**), keeps the ORM integrity checks, adds a stale-backup check against the RPO window · verify: `tests/integration/test_ops_commands.py::test_doctor_no_filesystem_assumptions`

> D14: **rehearse a restore before the first non-founder tenant.** Not automated, not optional — do
> it once by hand and write down how long it took. That number is the real RTO. An untested restore
> is not a backup. **Still unmet** — this is a human action G14 cannot discharge; recorded as an
> open gate in the final report, not faked here.

**G14.2 turned out not to need an S3 branch at all — the DB already holds every key.** The first
draft tarred the filesystem backend's storage root directly and refused outright on S3 (no `list()`
on the Protocol, deliberately — G11). That would have shipped hosted deployments, the *only* ones
D13 actually applies to, with no media backup whatsoever, against §11.1's "required regardless of
this decision." `build_key()` has exactly one caller (verified by grep, not assumed), so every
object is reachable from a `Document.file_path` row — enumerate `Document`, `storage.get()` each
key, archive it, `storage.put()` it back on restore. One code path, both backends, no Protocol
change. An external reviewer caught this before the first line of `backup.py` was written.

**What G14.3's stale-backup check actually checks, and what it cannot.** Step 14 also asks for a
check that the *managed Postgres provider's* last backup is within the RPO window (D14) — that
needs that provider's API, and D13 leaves the vendor as "an implementation detail," so nothing in
this codebase can name which API to call. Built instead: a check against **our own** media backups'
mtime, RPO taken as 24h from D14's "automated daily backups" until a real SLA sets a number. The
vendor-side gap is routed to `opportunities.md`, not faked — same treatment G12 gave A17.

**`doctor` is per-account, and now says so as its first line of output.** Every ops command binds
to one account (G13); a clean `doctor` run on a multi-tenant host read as "the install is healthy"
would be exactly the false pass A18 exists to prevent, one level up from what A18 names.

**Two bugs surfaced only by writing the tests, both fixed in the code they exposed, not the test:**
- `_live_service_pids()`'s `except ProcessLookupError` was POSIX-only. A pid with no matching
  process on Windows raises a plain `OSError` (`WinError 87`), not `ProcessLookupError` — the SQLite
  -era version of this exact check had carried the bug silently since SPEC-001. Fixed by catching
  `PermissionError` first (still "live"), then the broader `OSError` (now "gone"), rather than the
  narrower POSIX-only exception type.
- `Task.property_id` turned out to carry a real `ForeignKey("properties.id")` under
  `0001_pg_baseline` (checked, not assumed) — the SQLite-era orphan-tasks check and its
  `PRAGMA foreign_key_check` companion were written for a world where that FK was unenforced. The
  `PRAGMA` (Postgres syntax error) is gone; the orphan check stays as a sanity net against a direct
  database edit that bypasses constraints, with a comment saying plainly that no normal write path
  can trigger it — better than a comment that used to claim the opposite.

### [ ] G15 — test-suite migration — *dep: G9* · *condition C changes here*

- [x] G15.1 · §6 Step 15 · — · replace `conftest.py`'s in-memory SQLite engine with a Postgres fixture (`TEST_DATABASE_URL`, skipping when unset) + `account_a` / `account_b` fixtures · verify: fixtures import
- [x] G15.2 · §6 Step 15 · A23 · **keep the `session` fixture's name and semantics** — it now yields an account-scoped session. **43 of 95 files use it** (not 28 of 33); renaming means touching 43 files · verify: full suite green
- [x] G15.3 · §6 Step 15 · — · **reconcile the second conftest** — `tests/web/conftest.py` also builds SQLite (`StaticPool`) and the spec's Fixtures paragraph does not contemplate it · verify: `tests/web/` green
- [ ] G15.4 · §6 Step 15 · — · docker-compose Postgres (D12). **`docker-compose.yml` already exists** and builds the Home Assistant demo stack — this is a **modify, not a create**; clobbering it breaks that setup · verify: compose config valid, HA services intact

> **Record the new baseline the moment this group commits.** A skip is a red gate (conventions §0):
> if `TEST_DATABASE_URL` is unset the Postgres fixture skips, the suite reads green, and the
> criteria that prove tenant isolation never ran.

### [x] G16 — importer — *dep: G15* — *11 tests; 1,869 real rows imported, 0 dangling FKs*

- [x] G16.1 · §6 Step 16 · A19 · `mihomes import <sqlite-path>` with the int→UUIDv7 remap · verify: `tests/integration/test_importer.py::test_roundtrip_counts_and_fks`
- [x] G16.2 · §6 Step 16 · A20 · upload → **verify** → *then* commit · verify: `test_failure_leaves_nothing`, parameterised over all three failure points

**Verified against the real thing, not a fixture.** The spec's clause says *"dry-run against a copy
of the `telegram-bot` archive"*; the author's own 1,823-row install was available and is a stronger
test. Result on a **copy** (never the original):

```
1,869 rows inserted   12 skipped   0 dangling FKs   1,881 of 1,822 accounted for
```

**G11 is not a blocker and G16 did not wait for it.** Measured: the source has exactly **one**
`documents` row and its file is missing — the path is `/static/uploads/…`, the phantom `src/web/`
artifact from the fixed H26 bug. So this import moves zero files. The *ordering* is still built and
tested against a narrow `FileMover` interface, because A20's requirement is that **failure leaves
orphans, not dangling references** — a property of sequence, not of backend. G11's S3 provider slots
in behind the same interface.

### Six findings from real data, each of which would have been invisible in a synthetic fixture

1. **`vendors.property_ids` — silent loss of every vendor→property link.** All 59 vendors carry a
   non-empty JSON id-list and the source has **no `vendor_properties` table**: their database
   predates M14's normalisation. The first working importer reported one line — `dropped:
   property_ids` — and threw all 59 associations away. **Row counts were correct**, because what was
   lost was a *column*. Found by investigating that line instead of accepting it.
2. **The target is the FK authority, not the source.** The old schema declares
   `transactions.work_order_id` as a bare `INTEGER` with no `FOREIGN KEY`, so
   `PRAGMA foreign_key_list` never mentions it while the target has a real FK. The raw integer went
   into a UUID column → `cannot cast type smallint to uuid`.
3. **Six polymorphic column pairs, not three** — derived from the schema rather than hardcoded.
   `alerts.source_entity_id` and `work_orders.source_id` would both have been missed.
   **This corrects the G4 note:** four *tables* carry `entity_type`/`entity_id`, but six *columns*
   are polymorphic, and it is columns that need remapping.
4. **SQLite does not enforce `VARCHAR(n)`; Postgres does.** A book's 108-character title is its slug
   against `VARCHAR(100)`. Truncating is right, but a truncated **slug** can collide and trip
   `UNIQUE (account_id, slug)` — so uniqueness is preserved explicitly and every truncation is
   reported.
5. **Not every table has an `id`.** `staff_properties` (association) and `configurations` (natural
   key) do not — the fourth appearance of the Core-`Table`-has-no-surrogate-key blind spot.
6. **Type coercion is unavoidable:** SQLite booleans are `0`/`1`, its datetimes are text. Driven by
   the **target column's** Python type so a column added later is coerced without anyone
   remembering.

### Design decisions worth defending

**Dangling references are preserved, not repaired.** 118 of 505 `audit_log` rows point at entities
that no longer exist — normal, because an audit log **records deletions**. The remap mints a UUID for
any `(table, old_id)` pair on first sight whether or not the row exists, so two audit rows about
deleted task 47 still share one id. **One mechanism** covers real remaps and dangling ones.

**A skip cascades only through REQUIRED links, decided per column.** 14 rows have missing parents,
and the outcome depends on the *target*'s nullability: `insurance_policies.property_id` is nullable,
so 2 rows are saved unparented; `spaces.property_id` is NOT NULL, so 9 are skipped and the skip
propagates. A blanket skip would have discarded estate data for no reason — and a test of mine
initially asserted the *wrong*, stricter behaviour.

**Empty account only.** Re-running would duplicate rows or trip a unique constraint partway through,
leaving the half-imported account the spec's clause forbids. Refusing before any write is simpler
than making 1,800 inserts idempotent.

> **My own G10 guard caught my own new code**, and I fixed the layer rather than the symptom. The
> importer's `text(f'SELECT COUNT(*) FROM "{table}" …')` tripped the AST guard. The table names came
> from the registry so nothing was injectable — which is precisely the defence G10 rejected. Now a
> Core `select`. A guard that fires on its author is a guard that works.

> Object writes are **not** transactional with Postgres, which is the whole reason for that order.
> This is where the data-preservation gate lives for this set (conventions §2) — not in the
> baseline, which runs against an empty database.

### [x] G17 — the isolation test — *dep: all* · **the definition of done** — *11 tests, all 40 tables, mutation-verified*

**A21 is green, and — more importantly — it is green for the right reasons.** Every arm was
mutation-tested: the layer it verifies was deliberately broken and the arm was confirmed to fail.
That check found **two of four arms had no teeth**, which a passing suite would never have revealed.

```
mutation                        arm that must fail                        result
ORM filter disabled             test_orm_filter_alone_...                 fails correctly
RLS disabled on properties      test_raw_sql_cannot_reach_...             fails correctly
WITH CHECK (true)               test_cannot_insert_..._another_account    fails correctly
GUC never set (G9 off)          test_each_account_can_read_its_own_rows   fails correctly
```

### The two findings that a green A21 was hiding

**1. Defence in depth means a test exercising both layers verifies neither.** Disabling the G8 ORM
filter *entirely* left `test_cross_tenant_denied_all_models` green — because it runs on the
unprivileged role where **RLS also blocks the read**. It was asserting "something stopped A", not
"the ORM filter stopped A". Fixed by pinning each layer on the connection where it is the *only*
defence present:

| test | connection | sole defence |
|---|---|---|
| `test_orm_filter_alone_blocks_cross_tenant_reads` | owner/superuser — RLS inert | the ORM filter |
| `test_raw_sql_cannot_reach_another_tenant` | app role — no mappers | RLS |

**2. A suite of only negative assertions is satisfied by a system that returns nothing at all.**
Disabling the G9 GUC left *every* negative assertion green: with no GUC, RLS returns **zero rows**,
so "A cannot see B's rows" is trivially true. **Isolation looked perfect precisely because nothing
worked.** A tenancy layer that denies everything is not secure, it is broken — and it would have
shipped green. `test_each_account_can_read_its_own_rows` is the positive control that closes it, and
it earned its place immediately by catching a real defect (below).

> **Not a toothless test, a bad mutation — worth recording so the distinction is not lost.** My first
> `WITH CHECK` mutation (removing the clause) also left its arm green, and the cause is that
> **`WITH CHECK` is optional in Postgres: when omitted, the `USING` expression is used for the write
> case too.** So removing it does not disable write checking. `WITH CHECK (true)` is the real
> mutation, and the arm fails correctly under it. A mutation that changes no behaviour proves nothing
> about the test.

### Built to enumerate, not to sample

- **All 40 registry tables**, via a **type-driven seeder** rather than 40 hand-written fixtures: 114
  required columns, 34 UUID FKs, 6 enums, seeded parents-first in topological order. A hand-written
  list stops covering a table the day someone adds one, and the suite stays green — the A11 lesson
  from the pilot.
- The seeder **fails loudly** on any table it cannot seed, because an unseeded table is a table A21
  does not cover. It did exactly that on first run (`memberships`, `membership_property_scopes` FK
  into GLOBAL `users`), which is the behaviour wanted from it.
- `test_both_accounts_are_fully_seeded` then asserts every table really has rows for both accounts —
  without it, a silently-unseeded table makes every assertion about it pass trivially.
- `EXPECTED_TENANT_TABLE_COUNT = 40` is hardcoded **deliberately**, so a change to the registry has
  to be acknowledged at that line rather than absorbed.

### G17.3 / A22 — retargeted, and non-destructive

The spec names *"the three `ai/tools.py` call sites"*; that file has **zero** `text(` calls, so the
criterion has no target. Retargeted at the property A22 is actually about: a raw
`DELETE FROM audit_log WHERE timestamp < :cutoff` — the literal shape from `archive.py`'s retention
path — issued by one account cannot touch another's rows. Node id kept for traceability.

> **The positive control caught my own test polluting the fixture on its first run.** The A22 test
> originally committed that DELETE, destroying account A's audit rows, and
> `test_each_account_can_read_its_own_rows` failed with *"account A sees none of its own rows"*. Now
> the counts are asserted **inside** the transaction and rolled back: same property proven, no
> residue. That would have been the fourth test-pollution bug of this run.

### [ ] G-Final — compound-stop verification — *dep: all*

- [ ] G17.1 · §6 Step 17 · A21 · for **every** model in the registry: A can never read, update or delete B's rows — via ORM queries, ORM **bulk** `update()`/`delete()`, **and** raw `session.execute(text(...))` — and can never insert a row stamped with B's `account_id` (RLS `WITH CHECK` rejects it) · verify: `tests/integration/test_isolation.py::test_cross_tenant_denied_all_models`
- [ ] G17.2 · §6 Step 17 · A21 · the registry covers **all 37** tenant tables **including the two association tables** — assert positively against a hardcoded list, not a derived one · verify: `test_isolation.py` fails if a tenant table is missing from the registry
- [ ] G17.3 · §6 Step 17 · A22 · **RETARGETED: `services/archive.py`'s raw-SQL sites**, not `ai/tools.py`. The spec names three call sites in a file that now has zero `text(` calls; `archive.py:45,61` is where raw SQL defended by RLS alone actually remains · verify: `tests/integration/test_isolation.py::test_ai_tools_raw_sql_scoped` *(node id kept for traceability; docstring records the retarget)*

> **A21 is the phase's definition of done.** Treat a red A21 as a stop-the-run defect, not an
> ordinary failure — and check it by hand as well as by test. The pilot's A11 taught that a sampled
> assertion rots; this one must enumerate.

> ### ⚠ A21 MUST run on `app_engine`, not `_pg_engine` — read this before writing G17.1
>
> `_pg_engine` connects as `postgres`, a **superuser, and superusers bypass RLS unconditionally**,
> even with `FORCE ROW LEVEL SECURITY` (measured in G7: all rows returned with the GUC unset). An
> A21 written on that connection would exercise only the G8 `with_loader_criteria` ORM filter —
> and would therefore report **green on the criterion the spec says the whole phase hangs on**
> while proving nothing about the database-level guarantee.
>
> The tell is subtle in the direction that matters: the test *passes*. Use the `app_engine` /
> non-superuser fixture added in G7, and assert the role (`test_app_role_is_not_a_superuser` is the
> pattern). The raw-`text()` arm of A21 in particular is **only** defended by RLS — the ORM filter
> does not see raw SQL at all — so on a superuser connection that arm has no enforcement behind it
> whatsoever.

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
