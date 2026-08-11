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

### Two expected skips, declared by name

Conventions §0 says **a skipped test is a red gate**, so any skip has to be declared here or the
F.2 walk should flag it. Exactly two are expected for the duration of this spec:

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

> **Note the `alembic/env.py` exclusion is different and stays.** `IDENTITY_TABLES` is excluded
> there so `alembic revision --autogenerate` does not propose creating six tables in the SQLite
> tree — and G6 needs autogenerate *working* to build the baseline. That exclusion retires with the
> tree too, but it is load-bearing until then.

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

### [x] G1 — identity models — *no deps* — *16 tests; A2 green; metadata 38 -> 44 tables*

- [x] G1.1 · §6 Step 1 · — · `models/account.py`, `user.py` (**GLOBAL**), `membership.py`, `invite.py`, `session.py` (**GLOBAL**), `membership_property_scope` per §4.2 · verify: models import
- [x] G1.2 · §6 Step 1 · A2 · the **one-active-owner partial index** — `Index(..., unique=True, postgresql_where=text("role = 'owner' AND status = 'active'"))` (D4) · verify: `tests/unit/test_membership.py::test_one_owner_partial_index`

> `User` and `Session` are **global** — no `account_id`, no RLS. Both are read *before* account
> context exists; a tenant policy on `sessions` returns zero rows and **locks every user out**.
> `Account` has **no `owner_user_id`** — ownership lives in `memberships`, enforced by that partial
> index. N7: do **not** add `'invited'` to `memberships.status`; an invitee has no `user_id` yet.

### [ ] G2 — `TenantOwned` on 37 tables — *dep: G1*

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

### [ ] G3 — composite indexes lead with `account_id` — *dep: G2*

- [ ] G3.1 · §6 Step 3 · — · per-table index audit; every composite index leads with `account_id` · verify: `EXPLAIN` on representative queries shows an index scan, not a sequential scan
- [ ] G3.2 · §6 Step 3 · — · **merge with the four existing `__table_args__`** — `budget.py:21`, `event.py:54`, `note.py:11`, `tag.py:18` · verify: those four models' existing constraints still hold

> The spec says *"only one model declares `__table_args__` (`note.py:11`), so this is nearly
> greenfield."* **There are four.** Replacing rather than merging silently drops existing
> constraints.

### [ ] G4 — child-table drift guard — *dep: G2*

- [ ] G4.1 · §6 Step 4 · — · **real-FK children** (`task_schedules`, `transactions`, `template_items`, …) → composite FK `(account_id, parent_id)` → `(account_id, id)` · verify: `tests/integration/test_drift_guard.py`
- [ ] G4.2 · §6 Step 4 · A12 · **polymorphic, no FK** (F5: `alert`, `audit_log`, `document`, `note`, `tag_assignments`) — a composite FK is **impossible**; use a trigger, or accept app-only enforcement **and say so**. Do not silently skip · verify: `tests/integration/test_drift_guard.py::test_child_account_mismatch_rejected`

> F5's five tables carry `entity_type` + `entity_id` with **no `ForeignKey`** — verified: all five
> are bare `Integer`. The spec is explicit that skipping them silently is not an option.

### [ ] G5 — unique constraints — *dep: G2*

- [ ] G5.1 · §6 Step 5 · A3 · `UNIQUE (account_id, slug)` on each of the **16** `SlugMixin` classes (not 15 — `event.py` has two) · verify: `tests/unit/test_slug_scoping.py::test_slug_unique_per_account`
- [ ] G5.2 · §6 Step 5 · A4 · `tag.name` per-account · verify: `tests/unit/test_slug_scoping.py::test_tag_name_per_account`
- [ ] G5.3 · §6 Step 5 · — · **skip `task.py:90`** per F4. The `ha_entity` sub-task is a **NO-OP — that model does not exist** · verify: n/a, documented

### [ ] G6 — `0001_pg_baseline` — *dep: G3, G4, G5*

- [ ] G6.1 · §6 Step 6 · — · **convert the 37 models' PKs to UUID** — D2 locks UUIDv7 app-side via `mihomes.ids.new_id()` (shipped by SPEC-001, reused verbatim, **no DB-side default**). Measured: 37 tables still have `Integer` autoincrement PKs and **no §6 step names this** · verify: every tenant model's pk is PGUUID with an app-side default

> **G6.1 is a hard prerequisite for creating any of the new tables, not just tidiness.** G1 already
> introduced the mismatch: `membership_property_scopes.property_id` is `PGUUID` with a
> `ForeignKey("properties.id")`, while `properties.id` is still `Integer`. Metadata tolerates that
> — measured — but `CREATE TABLE` does not. So the identity tables cannot be created against a
> real database until this conversion lands, which is why G6 depends on G3/G4/G5 rather than
> running early. The mismatch is intentional and recorded rather than worked around: writing the
> new models with integer FKs would mean converting them again in G6.
- [ ] G6.2 · §6 Step 6 · — · the squashed baseline: identity + 37 domain + the 5 new, Postgres-native · verify: `upgrade` then `downgrade` clean on an empty Postgres
- [ ] G6.3 · §6 Step 6 · — · archive the **40** old revisions (not 36) to `alembic/legacy_sqlite/` — reference only, never run · verify: `alembic/versions/` contains only the new baseline; `alembic heads` reports one head
- [ ] G6.4 · §6 Step 6 · — · **`waitlist` is NOT in the baseline** · verify: a fresh main-tree upgrade creates **37 + identity + 5** tables and no `waitlist`

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
- [ ] G8.3 · §6 Step 8 · A7 · `before_flush` stamps `account_id` on insert · verify: `tests/unit/test_scoped_session.py::test_insert_stamped`
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

- [ ] G15.1 · §6 Step 15 · — · replace `conftest.py`'s in-memory SQLite engine with a Postgres fixture (`TEST_DATABASE_URL`, skipping when unset) + `account_a` / `account_b` fixtures · verify: fixtures import
- [ ] G15.2 · §6 Step 15 · A23 · **keep the `session` fixture's name and semantics** — it now yields an account-scoped session. **43 of 95 files use it** (not 28 of 33); renaming means touching 43 files · verify: full suite green
- [ ] G15.3 · §6 Step 15 · — · **reconcile the second conftest** — `tests/web/conftest.py` also builds SQLite (`StaticPool`) and the spec's Fixtures paragraph does not contemplate it · verify: `tests/web/` green
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
