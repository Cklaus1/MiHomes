# MiHomes — Opportunities (Deferred-Work Sink)

> Populated **during** the autonomous build loop. This is a capture buffer, not a work queue for the current run.
> It becomes the curated input to the **next** build loop.

## Rules (from the build harness)
- **Optimizations** discovered mid-build are logged here **one line each** and **NOT acted on** — they have no failing test, so acting on them violates the verification bar and risks new bugs in a bug-fixing pass.
- **Bugs** discovered mid-build that are outside the current DAG are captured here as **candidate tasks with a proposed severity** — never silently fixed outside the DAG.
- **Exception:** a fix that is impossible or clearly wrong without a minimal refactor may include that minimal refactor as part of its task (as R1–R5 anticipate). Note it here anyway.

---

## Optimizations (captured, not acted on)
<!-- format: `- [OPT] file:line — one-line description (surfaced during <task-id>)` -->
- [OPT] scripts/watchdog.py + cli/{telegram,whatsapp}.py — extract shared `watchdog_common.py` (spawn/liveness/log-handle helpers). Deferred from G0.2: after the telegram-bot merge only `scripts/watchdog.py` is a true reaping parent, so extraction is cosmetic now; revisit if a 2nd supervisor appears. (surfaced during G0.2)

## New bugs (candidate tasks for next loop)
<!-- format: `- [BUG][proposed-severity] file:line — title — concrete failure — proposed fix (surfaced during <task-id>)` -->
- [BUG][P2 — FIXED IN-RUN] services/weekly_report.py:258 `_assignee_name` — `report weekly --format markdown`/`15-5` crashed with `AttributeError: 'Staff' object has no attribute 'full_name'` whenever a task was assigned (terminal renderer never hit this path, so it was latent). Fixed to `s.name` during L4; regression covered by test_cli.py TestFormatEnumValidation.test_report_weekly_valid_format (exercises the markdown path). (surfaced during G-CLI/L4)

## DAG-omissions caught by F.3 reconciliation (surfaced during G-Final)
> F.3 walked the spec top-to-bottom and found 4 findings the DAG author never assigned to a group: H3, M7, M8, M9. Per compound-stop condition B each is now landed-with-test or deferred below.
- [BUG][H3 — FIXED IN-RUN] db.py:37 `get_engine` swapped `_engine` but left `_SessionLocal` bound to the old engine → `get_session()` silently talked to the previous DB (cli/init.py hand-poked the global as a workaround). Fixed: reset `_SessionLocal=None` on engine swap. Regression: `tests/unit/test_engine_swap.py` (2 tests). (surfaced during G-Final/F.3)
- [BUG][M8 — FIXED IN-RUN] services/archive.py:96,100,116,120 — raw-SQL used f-string `cutoff.isoformat()` (T-separated) while SQLite stores DateTime space-separated; `' ' < 'T'` made a row at exactly the cutoff boundary get archived+deleted though the ORM count (strict `<`) excluded it — a within-window row silently lost. Fixed: bound datetime params. Regression: `tests/unit/test_archive.py::test_raw_sql_delete_matches_orm_count_at_boundary`. (surfaced during G-Final/F.3)
- [BUG][M9 — CRASH FIXED IN-RUN, tz-half DEFERRED] services/calendar_sync.py:87 — a 23:00 appointment computed `end=datetime(hour=hour+1=24)` → ValueError swallowed by the bare except → appointment silently never synced. Fixed: `end = start + timedelta(hours=1)`. Regression: `tests/unit/test_calendar_sync.py::TestPushAppointmentLateHour`. **Deferred half:** the spec's "use the property's timezone instead of hard-coded 09:00 UTC" needs a `Property.timezone` column (schema change; R4 reconciliation migration is closed/committed) — see DEFER below.
- [BUG][M7 — DEFERRED] models/__init__.py:16-24 (+ 22 DateTime columns across 10 model files) — naive `DateTime` columns receive tz-aware values; a round-tripped `created_at` compared to `datetime.now(timezone.utc)` would raise `TypeError`. No live crash path today (comparisons that matter use bound params or ISO strings), but it is a latent class-wide hazard. **Deferred:** the fix (`DateTime(timezone=True)` on 22 columns, or standardize naive-UTC) is a schema-wide migration; R4 (the reconciliation-migration group) is closed and committed, and adding a 23rd/24th migration outside it violates minimal-impact for a latent-only bug. Top schema candidate for the next loop. (surfaced during G-Final/F.3)

## Optimizations (captured, not acted on) — added during G-CLI
- [OPT] models/ai_conversation.py:19 + services/ai/orchestrator.py:34 + services/archive.py:113 — dead `tokens_used` field (never populated; only copied by archive). Dropping it needs a migration for marginal gain → deferred rather than fixed in the L10 hygiene pass (minimal-impact). (surfaced during G-CLI/L10)

## Deferred features / larger refactors (seeded by Step 2 decisions)
<!-- Decided out of this hardening run; candidates for the next loop. -->
- [DEFER][Q6] `services/ai/agent.py`, `provider.py` — provider-agnostic tool loop (`stream_with_tools()` capability on the provider protocol) so non-Claude providers get the full tool path instead of the degraded no-tools fallback (M36). Top candidate next run — architecture change, no P0–P2 depends on it.
- [DEFER][Q3] `web/routes/reports.py` (deleted this run) — if a reports page is wanted, rebuild against the live schema + mount + nav. Do NOT resurrect the dead module.
- [DEFER][Q5] `models/ha_entity.py` (deleted this run) — Home-Assistant entity sync is a future feature; re-add model + migration + integration when HA is in scope.
- [DEFER][M7] all naive `DateTime` columns → `DateTime(timezone=True)` (or naive-UTC standardization) in one reconciliation migration. Schema-wide; latent-only today. Pair with the next R4-style migration group.
- [DEFER][M9-tz] `services/calendar_sync.py` + `models/property.py` — add `Property.timezone` and localize appointment/task push times instead of hard-coded 09:00/appointment-hour UTC. Needs a schema column; the crash half is already fixed. Do with the M7 migration.

## Deferred/blocked from this run
<!-- format: `- [BLOCKED] <task-id> — why blocked, what would unblock it` -->

---

# Spec build loops (SPEC-001 … SPEC-008)

> Seeded 2026-08-06 while authoring `tasks/build-loop-conventions.md` and
> `tasks/build-loop-spec001.md`. Nothing below was surfaced by a run — these are prerequisites
> and hazards found while reading the specs against `origin/main`.

## Blocking prerequisites

- [BLOCKED] SPEC-002 A23 + Step 17 — **no CI exists in this repo.** No `.github/`, no `Makefile`;
  `pytest -q` is run by hand. A23 requires "full suite green in CI" and Step 17's isolation test
  must "run against Postgres with RLS, **on every PR**". A harness can prove neither — it can
  only prove local green. **Unblocked by:** adding a workflow with a Postgres service container.
  Until then, any criterion whose wording requires CI must be reported as locally-green-only,
  never marked met. (surfaced while authoring build-loop-conventions §7)

- [BLOCKED] SPEC-006 P2 — **reconcile `telegram-bot` with `origin/main`; nobody owns it.**
  SPEC-006 §0.1: *"if this spec is built from `telegram-bot`, everything in §3–§5 is wrong"* —
  the shared gateway core `gateways/review_common.py` (1,175 lines) exists only on main. No
  autonomous loop resolves branch topology. **Unblocked by:** a human deciding whether
  `telegram-bot` is rebased onto main or retired. SPEC-006 stays unharnessed until then.
  (surfaced while authoring build-loop-conventions §3.3)

## Pre-existing test failure — baseline, not regression

- [BUG][P3 — WINDOWS-ONLY, DEFERRED] `tests/integration/test_backup.py::test_stale_pid_file_does_not_block_restore`
  — `os.kill(pid, 0)` is a POSIX signal-0 liveness probe; Windows raises
  `OSError: [WinError 87] The parameter is incorrect` instead of the expected `ProcessLookupError`.
  Fails identically on the untouched `be8d398` baseline, so **the Windows baseline is 1078
  passed / 1 failed / 1 skipped**, not the 1080-passing the hardening report recorded on another
  platform. **Deferred:** out of scope for every spec in the set, and fixing a platform quirk
  mid-spec-build violates minimal-impact. **Fix when convenient:** guard the probe with
  `psutil.pid_exists()` or catch `OSError` alongside `ProcessLookupError`. Recorded in
  `build-loop-spec001.md` §0.1 so the circuit breaker does not halt the pilot over it.
  (surfaced while measuring the baseline for condition C)

## Measured: the 40 SQLite-era migrations do not replay on Postgres

- [BUG][SPEC-002 Step 6 — SIZING, NOT OPTIONAL] `alembic/versions/` (all 40 revisions) — measured
  2026-08-06 against **PostgreSQL 18.4**: `alembic upgrade head` on an empty database fails at
  `e5f6a7b8c9d0_add_daily_recurrence.py` with
  `DataError: invalid input value for enum recurrencefrequency: "weekly"`. That revision's own
  docstring states the assumption — *"SQLite stores enums as VARCHAR, so no ALTER needed"* — which
  is true on SQLite and false on Postgres, where the enum type is real and stores member **names**
  (`WEEKLY`) after G-R4's server-default normalization.

  **Critically: revisions 1–27 are unvalidated, not passing.** Postgres has transactional DDL, so
  all 40 ran inside one transaction that rolled back entirely — verified `0` tables created and no
  `alembic_version` table afterwards. The first error masks every later one, so the true defect
  count can only be found by fix-and-retry and exceeds any static audit.

  **What this sizes:** SPEC-002 Step 6's squash to `0001_pg_baseline` is **required, not an
  optimization** — and the revisions it archives to `alembic/legacy_sqlite/` were never
  Postgres-viable to begin with, so "reference only, never run" is the correct disposition.
  (surfaced while pre-flight-testing the chain for SPEC-001 G2)

  **Full audit (rendered with `alembic upgrade --sql` against a `postgresql+psycopg` URL, so the
  SQL below is what Postgres actually receives, not inference):**

  *Root cause* — no `native_enum=False` and no `values_callable` anywhere, so every `sa.Enum(...)`
  becomes a real `CREATE TYPE`: **21 enum types**. Models are `class X(str, Enum)` with lowercase
  *values*, but SQLAlchemy persists member *NAMES*. `7514b34eed7b:176-181` documents this.

  *Three empty-DB blockers, all failing at parse/analyze time — data-independence is not a
  mitigation:*
  - `e5f6a7b8c9d0:21-29` — `frequency = 'weekly'`/`'daily'`: wrong case **and** `DAILY` is not a
    label of `recurrencefrequency` at all (created 7-label at `97b6fac21ea9:46`).
  - `ce1a992f291e:35-42` — three independent SQLite-isms in one statement: `INSERT OR IGNORE`
    (PG: `ON CONFLICT DO NOTHING`), `JOIN json_each(...)` with **no `ON` clause**, and `json_each`
    on a JSON array (PG: `json_array_elements`). Downgrade adds `json_group_array` + `sqlite.JSON()`.
  - `7514b34eed7b` — five distinct PG-fatal defects: `UPPER(status)` on an enum column
    (`:182-184`, → `function upper(ptostatus) does not exist`); `SET DEFAULT 'PENDING'`/`'GOOD'`/`'OK'`
    against types created with **lowercase** labels (`:197-205`); `SET frequency = 'DAILY'` (`:191-196`);
    and `ALTER COLUMN category TYPE taskcategory` where **`taskcategory` is never created anywhere
    in the chain** and a varchar→enum cast needs a `USING` clause Alembic does not emit (`:152-156`).

  *Two silent gaps — these would make a green `upgrade head` a **false pass**:*
  - `expensefrequency` (`7514b34eed7b:133-137`) renders as a bare `ALTER COLUMN ... TYPE` with **no
    `CREATE TYPE`** — a no-op against the existing 5-label type, so the migration reports success
    while `CUSTOM_WEEKS`/`CUSTOM_MONTHS` fail at runtime.
  - `consumablestatus` / `ptostatus` / `bookcondition` are created with lowercase labels while the
    ORM persists uppercase names — unusable by the app as created.

  *Notable clean results:* `b3f5c1d9a72e` (money int-cents) is **fine** — `CAST(ROUND(amount*100)
  AS INTEGER)` is valid PG and float8→int4 is an assignment cast needing no `USING`. 24 of 40
  revisions use `batch_alter_table`; none rely on SQLite rebuild semantics, and `env.py:91` already
  gates `render_as_batch=is_sqlite`. No `PRAGMA`/`julianday`/`strftime`/`AUTOINCREMENT` anywhere.

  *Beyond "exit 0", a correct schema also needs* `ALTER TYPE ... RENAME VALUE` ×3, `ADD VALUE` ×3,
  and `CREATE TYPE taskcategory` — **and `ALTER TYPE ... ADD VALUE` cannot run in the same
  transaction that adds it**, while `alembic.ini` sets no `transaction_per_migration`, so the whole
  chain is one transaction. That is a structural blocker for the patch approach, not a tweak.

  *Independent reason patching is wrong:* `tests/integration/test_migration_reconciliation.py` is
  SQLite-hardcoded (`import sqlite3` at :16, `sqlite:///` at :93, `pytest.raises(sqlite3.IntegrityError)`)
  and round-trips the full chain. Any patch to historical revisions would have to dialect-branch to
  stay green there *and* work on PG — for a database the single-user product does not yet use.

- [BUG][SPEC-001 §3 — MANIFEST CONTRADICTS D1/D3] The file manifest places the waitlist migration
  at `alembic/versions/xxxx_waitlist.py`, i.e. the single-user product's tree. That contradicts the
  spec's own decisions: **D1** says the landing app *"shares the stack and nothing else"* and
  **D3** says its database holds the **`waitlist` table only**. Following the manifest would
  replay 40 revisions and create all 37 single-user tables in the landing database — and would
  fail anyway, per the finding above.

  **Resolved in the harness, not the spec:** `tasks/build-loop-spec001.md` G2 now targets a
  separate `alembic_landing/` tree with `target_metadata` scoped to `Waitlist.__table__` alone
  (`Base.metadata` carries 37 tables — verified). No spec anywhere in the set mentions
  `version_locations` or a second tree, so this is a genuine gap rather than a documented
  alternative. **Worth folding back into SPEC-001 §3 by its author.**
  (surfaced 2026-08-06, decided with the founder)

## SPEC-002 pre-flight findings (2026-08-10) — five defects, two blocking

> Found by the conventions §3.1 re-verification gate before authoring
> `build-loop-spec002.md`. All verified against HEAD `714fa1a`, not inferred. Same class as
> SPEC-001 §3's manifest defect: **for the spec author to fold back into SPEC-002.**

- [BUG][SPEC-002 A22 + Steps 10, 17 — BLOCKING, NO TARGET] `services/ai/tools.py` contains
  **zero** `text(` calls (`grep -c "text(" src/mihomes/services/ai/tools.py` → 0). The hardening
  pass `9d6e02c` rewrote all three onto the ORM. Consequences: **A22**
  (`test_isolation.py::test_ai_tools_raw_sql_scoped`) cannot be written as specified; **Step 17**'s
  *"Must exercise the three `ai/tools.py` call sites by name"* is unsatisfiable; **Step 10**'s
  *"rewrite the three in `ai/tools.py`"* is already done. The tenancy concern **moved, it did not
  vanish** — `services/archive.py:45,61` still interpolates table names into `text()`, and
  `backup.py:203` runs `PRAGMA foreign_key_check`. **Fix:** retarget A22 and Step 10 at
  `archive.py`. A harness reading the spec literally would fabricate a test or stall.

- [BUG][SPEC-002 §4.3 + A1/A21 — BLOCKING, SILENT TENANCY HOLE] `staff_properties` and
  `vendor_properties` are Core `Table(...)` objects (`models/staff.py:10`, `models/vendor.py:11`),
  not declarative classes. Measured: **38 metadata tables but only 36 mapped classes.** Therefore
  (a) `TenantOwned` is a `@declared_attr` mixin and **cannot** apply to them, so no `account_id`;
  (b) §4.3 derives `TENANT_TABLES` from `TenantOwned.__subclasses__()`, so **no RLS policy is
  generated**; (c) A1 and A21 iterate that same registry, so **neither ever tests them**. That is a
  cross-tenant read/write surface with no application filter and no RLS backstop **while A21 — "the
  phase's definition of done" — reports green.** Precisely the failure A21 exists to prevent.
  **Fix:** the registry must enumerate Core association tables explicitly, not rely on
  `__subclasses__()`.

- [BUG][SPEC-002 Step 10 / A13 — VERIFY CLAUSE UNSATISFIABLE] Step 10's *"`grep -rn 'text(f"'
  src/` returns nothing"* can never go green: two of the four current hits are substring collisions
  on `*_text(f"` — `ai/orchestrator.py:290` (`SESSION_FILE.write_text(f"…")`) and
  `web/routes/ai.py:505` (`save_document_text(f"…")`). **Fix:** A13 needs a word-boundary or AST
  check, not this grep.

- [BUG][SPEC-002 §6 — UNNAMED WORK] **37 tables have integer PKs; only `waitlist` has UUID.** D2
  locks UUIDv7 app-side via `mihomes.ids.new_id()`. There is no in-place conversion step because
  there is no in-place conversion — Step 6 creates fresh UUID-native tables and Step 16 imports
  across with an int→UUIDv7 remap. Coherent, **but the 37 models' PK columns still have to change
  and no §6 step says so.** Also: Step 6 defers to "§5.4" and D9 to "§5.2", **neither of which
  exists in SPEC-002** (they are `MULTITENANCY.md` refs). **Fix:** name the model-side PK change in
  Step 6, and correct the two dangling section refs.

- [BUG][SPEC-002 — STALE COUNTS, ALL UNDERSTATING] Every count is low, so every estimate built on
  them is optimistic:

  | Spec claim | Reality at `714fa1a` | Effect |
  |---|---|---|
  | "the existing **33** test files pass" (Step 15, A23) | **95** | ~2.9× |
  | "**28 of 33** use the `session` fixture" (§9, F6) | **43 of 95** | the keep-the-name argument gets *stronger* |
  | "**36** domain tables" | **37** tenant-owned (38 − `waitlist`) | Steps 2–5, 7, A1, A21 |
  | "**36** revisions" archived (D9, §3) | **40** | `legacy_sqlite/` |
  | "only **one** model declares `__table_args__` … nearly greenfield" (Step 3) | **four** — `budget.py:21`, `event.py:54`, `note.py:11`, `tag.py:18` | three must be *merged with*, not replaced |
  | F3 "`SlugMixin` … **15** models" | **16** classes (`event.py` has two) | Step 5 is 16 constraints |
  | F4 cites `ha_entity.py:21` | **no `ha_entity` model exists** | that sub-task is a no-op |

  Single head confirmed `4db594964c82`. A naive regex reports two heads because `f1e2d3c4b5a6`
  has a tuple `down_revision` — artifact, not a branch; do not re-derive it.

## Found while RUNNING SPEC-002 (2026-08-11)

- [BUG][SPEC-002 §6 — STEP ORDER IS NOT EXECUTABLE AS LISTED] **Step 2 breaks every test in the
  suite and Step 15 is what fixes them, thirteen steps later.** Measured at G2: applying
  `TenantOwned` makes `account_id` NOT NULL across **40** tables, and the affected-area suite went
  **187 failed / 156 errors**, every one `NOT NULL constraint failed: <table>.account_id` — nothing
  supplies an account until Step 15's `conftest.py` fixture seeds one and binds the ContextVar.

  Following §6's numbering literally means twelve groups (Steps 3–14) run against a red suite,
  which makes the per-group regression gate meaningless for all of them. **Fix:** §6 should either
  order Step 15 immediately after Step 2, or state explicitly that the suite is expected red in
  between and that the regression gate is suspended until Step 15 lands.

  Resolved in the harness by running G15 straight after G2 — legitimate, because G15's declared
  dependency is `G9`, not "all previous", and none of its sub-tasks' verify clauses need RLS or the
  scoped session. Recorded in `build-loop-spec002.md` §1 so a restart can explain the ordering.

  Worth noting N1 does *not* cover this. N1 warns that Steps 2–5 are four passes over the same
  tables — a sizing warning. This is a different and larger finding about executability.

- [BUG][SPEC-002 §6 Step 2 / §4.3 — TABLE COUNT IS 40, NOT 37] Measured at G2 against HEAD: 40
  tenant-owned tables, 4 global (`users`, `sessions`, `waitlist`, plus `accounts` as the tenant
  root). The spec's 36/37 predates Phase 0 and counts only the domain tables, omitting `invites`,
  `memberships` and `membership_property_scopes` — which its own **Step 1** adds. Every per-table
  estimate in Steps 2–5 and §4.3 is low by three.

## Resolved during SPEC-002 pre-flight (not defects — decisions)

- [DEFER][waitlist ownership] SPEC-002 mentions `alembic_landing`, `version_locations` and
  `_UNMANAGED_TABLES` **zero times**; `waitlist` appears once, in D3's global-table list. Decided
  with the founder 2026-08-10: **`0001_pg_baseline` covers the 37 tenant/identity tables and omits
  `waitlist`; `alembic_landing/` keeps owning it.** Preserves D1 (*"shares the stack and nothing
  else"*) and D3 (*"`waitlist` table only"*). Worth folding into SPEC-002 §3 by its author.

- [OPT] `alembic_landing/env.py` + `src/mihomes/landing_migrations.py` — the landing tree now sets
  an explicit `version_table = "alembic_version_landing"`. Both trees previously defaulted to
  `alembic_version`: harmless while the landing app has its own database, a hard collision if they
  ever share one. Ours, not the spec's. (surfaced during SPEC-002 pre-flight)

- [OPT] **`ruff check .` reports 565 findings across the pre-existing tree** — 175
  unsorted-imports, 168 raise-without-from, 149 unused-import, and the rest. **None are in
  SPEC-001/002 code** (verified: the 37 spec-introduced files pass clean). The
  `.pre-commit-config.yaml` hook only ever linted *staged* files, so the older tree was never
  checked whole. Two findings are false alarms rather than debt: `custom_components/` is a Home
  Assistant component running on HA's interpreter, so its `type` alias is flagged against the wrong
  target version; and the two `F821`s are string forward-references whose import lives inside the
  function. **CI therefore lints only spec-introduced files** (`git diff` against the merge-base,
  self-maintaining). Cleaning the 565 is a real task, worth doing on its own branch where the diff
  is legible. (surfaced while adding CI during SPEC-002 pre-flight)

## Spec defects found while authoring the harnesses

- [BUG][SPEC-002 §9 — STALE REF] `docs/specs/SPEC-002-phase1-multitenant-foundation.md` §9 and
  Step 15 assert *"the existing **33** test files pass"* and *"**28 of 33** existing files use
  the `session` fixture"*. True on `telegram-bot` (33 files), **false on `origin/main` (82
  files)** — understated 2.5×. A harness targeting main could mark Step 15 done with 49 other
  test files broken. Not fixed in the spec (that is the author's call); **guarded** by
  conventions §3.1's pre-flight re-verification gate, which halts on the mismatch.
  (surfaced while authoring build-loop-conventions §3.1)

- [BUG][SPEC-001 §9 — STALE REF] Same class: *"the **780+** existing tests depend on it"*.
  `origin/main` collects **1080**. Same guard applies. (surfaced while authoring build-loop-spec001)

- [BUG][SPEC-003 §1.3 — WRONG POINTER] O1 is described as blocking *"Step 13's write path only"*,
  but §6 Step 13 is the account switcher; the config UI carrying O1 is **Step 15**. Trust §6.
  Corrected in conventions §4.3 rather than edited into the spec.

- [BUG][SPEC-006 §1.3 — WRONG POINTER] O1 is described as *"the WhatsApp half of Step 8"*, but
  Step 8 is `notify_staff`'s fallback; the Cloud API work is **Step 7**. Additionally its exit
  check says *"Steps 0–11"* while §6 defines Steps 1–10 plus two lettered prerequisites — an
  off-by-two in both directions. Corrected in conventions §4.3.

## Deferred — not this work

- [DEFER][ui-frontend] Branch `ui-frontend` has **51 unpushed commits** and no configured
  upstream (local `4c4dd39` vs `origin/ui-frontend` at `968bda0`). Unrelated to the spec build,
  but it is the only unpushed work in the repo and will drift further. Decide: push, merge, or
  retire. (surfaced while surveying branch divergence)

- [DEFER][telegram-bot] `telegram-bot` is diverged from main (30 ahead / 13 behind) and is
  **behind main on gateway code** — main's G-R2a/G-R2b hardened the gateways further. Its unique
  content is UI/frontend polish that main's G-Web pass rewrote in the same files (3 conflicts:
  `scripts/watchdog.py`, `web/routes/vendors.py`, `web/templates/dashboard.html`). Resolving this
  is also what unblocks SPEC-006 P2 above.
