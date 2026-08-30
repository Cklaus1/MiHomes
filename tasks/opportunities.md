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
- [BUG][P2 — NOT FIXED, OUTSIDE THIS DAG] `scripts/start-mihomes.sh` — checked out with **CRLF** line
  terminators on this Windows machine (`core.autocrlf=true`; measured with `file scripts/start-
  mihomes.sh`). `docker-compose.yml`'s `mihomes` service bind-mounts this file straight from the
  host filesystem into a Linux container (`./scripts/start-mihomes.sh:/start.sh:ro`) and runs it as
  `sh /start.sh` — a CRLF-terminated script fed to `sh` this way typically fails per-line (`$'\r':
  command not found` or similar) rather than merely warning. Anyone running `docker compose up` for
  the HA demo stack from a Windows checkout would hit this, independent of and in addition to S7
  (the SQLite/`LookupError` bug in the same startup path). **Fix:** `git add --renormalize
  scripts/start-mihomes.sh` after adding a `.gitattributes` `*.sh text eol=lf` rule (added this run,
  in G15.4, to stop the new `docker/postgres-init/01-create-databases.sh` from suffering the same
  fate) — not done here because the file's content is untouched by G15.4 and outside its DAG; the
  `.gitattributes` rule alone fixes it for anyone who re-clones, but not for an existing Windows
  working tree until it is re-added. (surfaced during G15.4)

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

- [BUG][SPEC-008 pre-flight — P2, NOT FIXED] **`httpx` is imported at runtime by four modules but
  is a `dev` extra, not a runtime dependency.** Measured 2026-08-30: `pyproject.toml:52` puts
  `httpx>=0.27` inside `[project.optional-dependencies]` (line 36), while
  `services/gateways/whatsapp/cloud_client.py:201`, `services/ha_sync.py` (4 sites) and the orphan
  `services/webhook.py:13` all import it. A clean `pip install -e .` therefore raises `ImportError`
  at first use — for `cloud_client.py` that is the moment someone sends a WhatsApp message, since
  `_post` is `pragma: no cover` and no test executes it.

  **This is SPEC-008's F2 holding, not failing** — the spec predicted exactly this state. The fix
  belongs to SPEC-008 rather than to a SPEC-006 amendment: §3's "New — web access (D8)" builds
  `web_tools.py` on outbound HTTP, so promoting `httpx` to runtime dependencies is that spec's own
  work, and the same one-line edit closes all four call sites at once. Reopening a complete,
  pushed, reported spec for an import CI never executes would be scope creep on a delivered
  artifact.

  Note `services/webhook.py:13` imports it at **module level**, so that orphan's 52 tests fail
  outright without the dev extra — a second concrete argument for deleting the three orphan
  modules from `62f1cb2`. (surfaced during the SPEC-008 pre-flight)

- [BUG][SPEC-006 G4 — P2, NOT FIXED, OUTSIDE THIS DAG] `services/gateways/review_common.py:372`
  — **a failing AI call mid-batch rolls back every *successful* prior write in the same batch.**
  `ai_response` catches any exception and calls `session.rollback()`. Its docstring (H27) says the
  rollback exists so *"a half-applied transaction does not poison every subsequent create in the
  same batch"* — but the measured effect is that it also discards the **completed** ones.

  **Found by measurement, not by reading.** SPEC-006 A11 walks all 15 `REVIEW_SCHEMA` categories
  in one loop; the `question` branch's AI call fails without an API key, and the resulting
  rollback took eleven prior categories' rows with it — every category read as "wrote nothing".
  `dispatch_items` still returned `logged: 1` for each, so the caller is told the write
  succeeded, the sender gets a confirmation, and the row is gone. In production this fires
  whenever the AI provider is down, rate-limited, or unconfigured: a batch containing one
  question silently loses the issues and tasks logged before it.

  A11 stubs `ai_response` rather than working around the side effect. Recorded rather than fixed
  because it is **not a tenancy bug** — widening G4 to cover it would grow a security group into
  a transaction-semantics refactor. **Proposed fix:** a SAVEPOINT per item
  (`session.begin_nested()`), so one item's failure rolls back only that item. (surfaced during
  SPEC-006 G4.1)

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

## Found while RUNNING SPEC-002, G3–G8 (2026-08-13)

> **These were fixed in the implementation and documented in `build-loop-spec002.md`, but that
> is the *builder's* artifact.** They are routed here because SPEC-002 §0.1's own warning is
> that *"divergence compounds — if SPEC-002 is implemented differently than specified, every
> spec above it inherits the difference"*, and SPEC-003…008 are written against this design.
> The two §4.4 defects in particular are **copy-pasteable code in the spec body**: anyone
> building from §4.4 as written gets code that raises on its first query.

- [BUG][SPEC-002 §4.4 — THE CRITERIA LAMBDA DOES NOT RUN] §4.4's scoped-session snippet writes
  `with_loader_criteria(TenantOwned, lambda cls: cls.account_id == current_account.get(), ...)`.
  SQLAlchemy rejects it outright, on the first ORM query:

  ```
  InvalidRequestError: Can't invoke Python callable get() inside of lambda expression argument
  ...; lambda SQL constructs should not invoke functions from closure variables to produce
  literal values since the lambda SQL system normally extracts bound values without actually
  invoking the lambda or any functions within it. Call the function outside of the lambda and
  assign to a local variable that is used in the lambda as a closure variable...
  ```

  Measured at G8 by implementing §4.4 verbatim; reverting to it fails 4 tests. **Fix:** hoist the
  read out of the lambda — `account_id = current_account.get()` on the line above, then
  `lambda cls: cls.account_id == account_id`. That is the form SQLAlchemy's own error message
  prescribes, and it keeps the fail-closed `LookupError` firing per statement.

- [BUG][SPEC-002 §4.4 — BLOCKING FOR G12 AUTH] **The filter as specified makes sign-in
  impossible.** §4.4 guards only on `state.is_select or state.is_update or state.is_delete`, so it
  calls `current_account.get()` for **every ORM statement in the process** and raises
  `LookupError` whenever no account is bound. But:

  - `users` and `sessions` are GLOBAL *precisely because* authentication must read them **before**
    any account exists (D3). Sign-in therefore cannot complete — the same bootstrap problem
    §4.2's `membership_self` policy exists to solve, one layer up, which the spec solves for RLS
    and not for the ORM filter.
  - `waitlist` belongs to the standalone landing app, whose sessions are the same `Session` class
    the listener binds to. Implementing §4.4 literally broke **all 10 SPEC-001 landing tests**,
    which is how this was found.

  **Fix:** return early when the statement involves no tenant-owned entity —
  `if not any(issubclass(m.class_, TenantOwned) for m in state.all_mappers): return`. `all_mappers`
  covers top-level entities, so a join *from* a global table *to* a tenant table still includes the
  tenant mapper and stays filtered; without that caveat "start the query from a global entity"
  would be a bypass.

- [BUG][SPEC-002 §4.3 / Step 7 — THE RLS PREDICATE CAN RAISE] The specified predicate,
  `current_setting('app.current_account', true)::uuid`, raises
  `invalid input syntax for type uuid: ""` when the GUC is set to the **empty string**.
  `missing_ok` (the `true`) only covers an *absent* setting. An error is the one outcome Step 7's
  verify clause rules out ("zero rows, not an error"). **Fix:** wrap in `NULLIF(..., '')`.
  This is a live constraint on **Step 9**, which owns the pool-checkin `RESET` — clearing tenant
  context by assigning `''` is a natural way to write a reset and would turn every subsequent
  query into a 500. Step 9 should clear with `RESET` / `set_config(..., NULL, ...)`.

- [BUG][SPEC-002 Step 7 — THE VERIFY CLAUSE IS UNSATISFIABLE ON A SUPERUSER CONNECTION, AND THE
  SPEC NEVER SAYS SO] Step 7 says *"connected as `app`"*, which is correct but easy to read as
  incidental. It is load-bearing: **superusers bypass RLS unconditionally, and `FORCE ROW LEVEL
  SECURITY` does not change that** — FORCE binds the table *owner*, not a superuser. Measured: as
  `postgres` with the GUC unset, a FORCE-protected table returned every row.

  The consequence is a false green, not a failure. Any test suite that connects as a superuser —
  which is the default for a local Postgres — passes **whether or not RLS exists at all**. In this
  run that silently covered 1366 tests before G7. **This is most dangerous for A21**, the spec's
  stated definition of done: run as a superuser it exercises only the §4.4 ORM filter while
  reporting that RLS holds, and A21's raw-`text()` arm is defended by RLS *alone*.
  **Fix:** §11/§13 should require a dedicated non-superuser role for the test harness, and A21
  should assert `NOT usesuper` on its own connection before asserting isolation.

- [BUG][SPEC-002 §4.4 + §4.3 — THE TWO ASSOCIATION TABLES EVADE *EVERY* ORM-LEVEL MECHANISM]
  Already logged for §4.3/A1 above (no `account_id`, no policy from a `__subclasses__()`-derived
  registry). Running it surfaced that the blind spot is **threefold**, not one item:
  `TenantOwned` is a `declared_attr` mixin and cannot apply to a Core `Table`; `before_flush`
  iterates `session.new` and Core inserts produce no instance; and `with_loader_criteria` takes a
  mapped class, so the read filter cannot reach them either. **Their only protection is RLS**,
  which per the finding above is only real on a non-superuser connection. **Fix:** §4.4 should say
  so explicitly, and name the column-level `default=` that stamping requires for Core tables.

- [BUG][SPEC-002 Step 4 — F5 NAMES FIVE POLYMORPHIC TABLES; THERE ARE FOUR] `alerts` is not
  polymorphic — it carries a real `property_id` FK and is fully drift-guardable. The four are
  `notes`, `documents`, `audit_log`, `tag_assignments`. **Note this discrepancy runs the opposite
  way to the others: the spec *understated* existing protection.** Also, `tag_assignments` belongs
  to *both* classes (real `tag_id` FK plus polymorphic `entity_id`), so filing it once misses half
  its exposure.

- [BUG][SPEC-002 Step 4 — THE COMPOSITE FK IS NOT IMPLEMENTABLE AS SPECIFIED] Step 4 prescribes a
  composite FK `(account_id, parent_id) → (account_id, id)` for the real-FK children. Measured at
  G4 across 52 links: adding it *alongside* the existing single-column FK gives two FK paths
  between the same tables and SQLAlchemy raises `AmbiguousForeignKeysError`; *replacing* the
  single-column FK does configure, but makes `account_id` a write target for every relationship
  into the child, and SQLAlchemy warns that sibling relationships conflict over it — silencing
  that needs an `overlaps=` annotation on all **53** of the codebase's relationships.
  **Fix:** Step 4 should offer the trigger as the primary mechanism rather than the
  polymorphic-only fallback. A trigger delivers the same database-level guarantee, needs no
  `UNIQUE (account_id, id)` on the 18 referenced parents (18 fewer indexes), and reaches the Core
  association tables that a `__table_args__` edit cannot.

- [BUG][SPEC-002 Step 5 — THE VERIFY CLAUSE IS NOT SATISFIABLE BY SCHEMA CHANGES ALONE] Step 5's
  clause is *"two accounts can each create a 'main-house' property and a 'Plumbing' tag"*. With
  `UNIQUE (account_id, slug)` correctly in place it still failed: `ensure_unique_slug()` searched
  the whole table with no account filter, so the second account got `main-house-2`, and
  `create_tag()`'s get-or-create matched on `name` alone and **returned account A's Tag row to
  account B** — handing over a foreign primary key rather than failing to insert. **Fix:** Step 5
  should name the service-layer functions that enforce uniqueness as part of its scope. There is a
  latent dependency on Step 8 for any verify clause that runs through a service getter.

- [BUG][SPEC-002 Step 6 — AUTOGENERATE DOES NOT PRODUCE A RUNNABLE BASELINE] Three gaps, all
  measured at G6.2: (a) `op.drop_table` does not drop the Postgres **enum type** it created, so
  `upgrade → downgrade → upgrade` — Step 6's own verify clause — fails on "type already exists"
  until `downgrade()` drops all 22 explicitly; (b) autogenerate renders the custom `Money` type as
  a fully-qualified `mihomes.type.money.Money()` and **emits no import**, so the generated file
  raises `NameError`; (c) the pre-existing `IDENTITY_TABLES` exclusion in `alembic/env.py` must be
  **removed before** autogenerating, or the baseline is silently missing all six identity tables.
  **Fix:** Step 6 should list these as required manual edits rather than implying `--autogenerate`
  output is complete.

- [BUG][SPEC-002 Step 9 — THE POOL `checkin` `RESET` IS NOT IMPLEMENTABLE] Step 9 prescribes a pool
  `checkin` `RESET` alongside the `after_begin` GUC. Measured at G9: executing SQL in SQLAlchemy's
  `checkin` event leaves an implicit transaction open on the psycopg connection, and SQLAlchemy's own
  connection reset — which restores the isolation level, i.e. sets `autocommit` — then fails with
  `can't change 'autocommit' now: connection in transaction status INERROR`, breaking every fixture
  that shares the pool. `RESET` is also itself transactional, so one issued inside a transaction that
  is subsequently rolled back is undone.

  **Fix:** drop the checkin RESET and have `after_begin` stamp the GUCs **unconditionally** —
  the bound value, or `NULL` when nothing is bound. A transaction-local `set_config(guc, NULL, true)`
  overrides a session-level value for the duration of the transaction (measured), so a stray `SET`
  cannot be observed by a scoped query. This is strictly stronger than the checkin reset: the
  guarantee holds at the point of use rather than depending on the pool having cleaned up.

- [BUG][SPEC-002 §4.4 — THE `after_begin` SNIPPET SETS ONLY ONE OF THE TWO GUCS] §4.4 sets
  `app.current_account` and never `app.current_user`, but §4.2's `membership_self` policy — the one
  bootstrap exception, which exists so the account picker can work *before* an account is chosen —
  keys on `app.current_user`. As specified, that policy is permanently unsatisfiable and the picker
  returns an empty list, presenting as *"sign-in works but you belong to no accounts"*. **Fix:** stamp
  both GUCs in `after_begin`, independently — a user may be bound with no account, which is precisely
  the pre-picker state.

  Related: §4.4's snippet `return`s early on `LookupError` and leaves the GUC untouched. Combined with
  connection reuse that is how a stale value survives; stamping `NULL` instead is what makes the
  absence explicit.

- [INFO][SPEC-002 §4.3 / Step 9 — WHERE THE EMPTY-STRING GUC COMES FROM] Closing the loop on the
  `NULLIF` finding logged above: after a **transaction-local** GUC's transaction ends,
  `current_setting('app.current_account', true)` returns `''`, not `NULL` (measured at G9). Every
  transaction after the first on a reused pooled connection is in that state, so
  `NULLIF(current_setting(...), '')::uuid` in the RLS predicate is **required for correctness**, not
  defensive — without it the second transaction on any pooled connection raises
  `invalid input syntax for type uuid: ""` instead of returning zero rows. Worth stating in §4.3
  beside the predicate, since the naive form looks correct and fails only under reuse.

- [BUG][SPEC-002 Step 6 CONSEQUENCE — ARCHIVAL HAS NO TABLES, AND THE SPEC NEVER ACCOUNTS FOR IT]
  `audit_log_archive` and `ai_conversations_archive` were created by a raw-SQL revision in the
  SQLite chain and were never on `Base.metadata`. Step 6 squashes that chain, and
  `0001_pg_baseline` does not create them — so **no migration in the resulting tree creates these
  tables at all.** Measured at G10: `run_archival()` raises
  `UndefinedTable: relation "audit_log_archive" does not exist`.

  Step 6 *revealed* this rather than caused it (the dependency on unmanaged tables was always going
  to break on a fresh deploy), but the spec squashes the chain without noting that two tables leave
  the schema. **Fix:** Step 6 should enumerate the raw-SQL-managed tables the squash drops, and
  either bring them into the baseline **tenant-aware** or state that the feature depending on them
  is out of scope for Phase 1.

  They cannot simply be recreated as they were: `id` is `INTEGER` while D2 makes every source id a
  UUIDv7, so `INSERT INTO audit_log_archive (id, …) SELECT id, …` cannot succeed; and they have no
  `account_id`, so archived rows would sit in a table with no tenant, no registry entry, no RLS
  policy and no drift-guard link. Gated with an explicit error at G10 pending a retention decision.

- [BUG][SPEC-002 Step 10 — THE `text()` CENSUS UNDERSTATES THE TENANCY PROBLEM] Step 10 frames the
  raw-SQL audit as removing **interpolation**. The interpolation in `archive.py` was never
  injectable (table names came from a hardcoded dict); the actual defect is that a raw `text()`
  statement **has no mappers, so the §4.4 ORM filter cannot see it** — those `SELECT COUNT(*)`
  queries returned cross-tenant totals, and `run_archival`'s `DELETE FROM audit_log` is scoped by
  **RLS alone**. **Fix:** Step 10 should be "rewrite raw SQL onto the ORM wherever a mapped model
  exists, and enumerate what remains as RLS-only" rather than "remove f-strings". A13's static
  guard catches the spelling; it cannot catch the exposure.

- [INFO][SPEC-002 — `except Exception:` AROUND SQL IS A POSTGRES LANDMINE] Not a spec defect, but it
  will bite anyone porting this codebase per §6: a broad `except` around a failing statement is safe
  on SQLite and harmful on Postgres, where the failed statement aborts the whole transaction and the
  **next** unrelated query fails with `InFailedSqlTransaction`. Measured in `archive.py`'s
  `except Exception: archived = 0`, which turned a missing table into an error several frames away.
  Worth a line in §11's migration notes: make optional statements optional with a `SAVEPOINT`.

- [BUG][SPEC-002 Step 16 — THE IMPORTER NEEDS SIX THINGS THE STEP DOES NOT MENTION] Step 16 describes
  the importer as "read SQLite, build the int->UUIDv7 remap table per source table". Measured while
  building it against a real 1,823-row install, the remap is the easy half. Each of these breaks the
  import or loses data, and none is implied by the step:

  1. **A legacy JSON id-list must be expanded into its association table, or associations are lost
     silently.** All 59 vendors in the source carry a non-empty `vendors.property_ids` JSON blob and
     the source has no `vendor_properties` table (it predates the M14 normalisation). Treated as "a
     source column with no target column", every vendor-to-property link disappears **with correct
     row counts**, because what is lost is a column rather than rows.
  2. **The TARGET schema is the foreign-key authority, not the source.** The old schema declares
     `transactions.work_order_id` as a bare `INTEGER` with no `FOREIGN KEY`, so
     `PRAGMA foreign_key_list` omits it while the target has a real FK — the raw integer lands in a
     UUID column (`cannot cast type smallint to uuid`).
  3. **There are six polymorphic column pairs, not the four tables F5 names.**
     `alerts.source_entity_id` and `work_orders.source_id` are polymorphic too. F5 counts *tables
     carrying entity_type/entity_id*; the importer needs *columns*.
  4. **SQLite does not enforce `VARCHAR(n)` and Postgres does**, so real data violates target
     lengths. Truncation is needed — and a truncated **slug** can collide, tripping
     `UNIQUE (account_id, slug)` and failing the import for a cosmetic reason.
  5. **Not every table has a surrogate `id`** (`staff_properties`, `configurations`), so any
     per-row logic keyed on `id` raises `no such column: id`.
  6. **SQLite/Postgres type coercion is mandatory**: booleans are stored as 0/1 and datetimes as
     text. Best driven by the target column's Python type rather than a per-column list.

  **Fix:** Step 16 should name these as required work. A harness reading the step literally builds an
  importer that runs, reports correct counts, and quietly drops a relationship.

- [BUG][SPEC-002 Step 16 — "NO PARTIAL ACCOUNT" NEEDS AN EMPTY-ACCOUNT PRECONDITION] The verify clause
  requires that "a simulated mid-import failure leaves no partial account", but the step does not say
  what happens on a **re-run**. Importing into an account that already holds data either duplicates
  every row or trips `UNIQUE (account_id, slug)` partway through — producing exactly the partial
  account the clause forbids. **Fix:** state that the import targets an empty account and refuses
  otherwise, checked before any write. Idempotency across 1,800 rows is the alternative and is far
  more machinery for no benefit.

- [INFO][SPEC-002 Step 16 — G11 IS NOT A PREREQUISITE] Step 16's ordering (upload, verify, then
  commit) reads as though object storage must exist first. It does not: the ordering guarantee is
  about *sequence*, so it is fully implementable and testable against a narrow file-mover interface
  with a filesystem backend, and G11's S3 provider slots in behind it unchanged. Worth noting in §6 so
  the importer is not blocked behind storage — especially since a real source may contain no movable
  files at all (this one contains exactly one document row, whose file is missing).

- [BUG][SPEC-002 Step 17 / A21 — THE CRITERION AS WRITTEN CAN BE MET BY A SUITE WITH NO TEETH] A21 is
  the spec's definition of done ("if it is not green, Phase 1 is not finished"), but it specifies only
  what must be *denied*. Building it exactly as written produced a suite where **two of four arms
  passed with the enforcement they check switched off**. Both causes are general, not incidental:

  1. **Defence in depth hides the failure of either layer.** SPEC-002 builds two independent
     boundaries (the §4.4 ORM filter and §4.3 RLS). An A21 run on the unprivileged role exercises
     both at once, so disabling the ORM filter entirely changes nothing observable — RLS catches it.
     The test asserts "something denied this", never "this specific layer denied this". **Fix:** §8
     should require each layer to be verified on the connection where it is the *only* one present —
     the ORM filter on a privileged connection where RLS is inert, RLS via raw SQL where the ORM
     filter is blind.
  2. **A21 needs a positive control, and the spec does not ask for one.** With the Step 9 GUC never
     set, RLS returns zero rows, so every "A cannot see B" assertion is trivially true and the suite
     is fully green while the product is completely broken. **Fix:** add "each account can read its
     own rows in every tenant table" to A21. A tenancy layer that denies everything satisfies the
     criterion as currently worded.

  Both were found by mutation-testing the finished suite, which is worth adding to §8 as the standard
  for A21 specifically: break each control, confirm the corresponding assertion fails.

- [INFO][SPEC-002 §4.3 — `WITH CHECK` IS OPTIONAL IN POSTGRES, WHICH MATTERS FOR REVIEWING A9] When a
  policy omits `WITH CHECK`, Postgres uses the `USING` expression for the write case as well. So a
  policy written with `USING` alone still rejects a foreign-account insert, and *removing* `WITH
  CHECK` is not a way to test A9 — the honest mutation is `WITH CHECK (true)`. Worth stating beside
  A9 so a reviewer does not read an explicit `WITH CHECK` as the only thing preventing cross-tenant
  writes, nor conclude from its removal that nothing changed.

- [BUG][SPEC-002 Step 11 / A14 — THE PRE-EXISTING STATIC MOUNT IS A LIVE CROSS-TENANT HOLE, AND THE
  STEP DOES NOT MENTION IT] Step 11 describes adding a `StorageProvider` and turning
  `Document.file_path` into an opaque key. It says nothing about how documents are **served**, and
  that is where the exposure is: `web/app.py` mounts the uploads directory via
  `SecureStaticFiles` with no authentication and no tenant check, so any request reaching the app can
  fetch any tenant's document. Filename obscurity was the only barrier and it was partial — generated
  reports were named `{title-slug}-{8 hex}`, i.e. 32 bits attached to user-visible text.

  **Fix:** Step 11 should require *removing* the static mount (not narrowing it) and serving objects
  through a route that authorises on the key's account prefix before reading bytes, and A14's wording
  should say "never world-readable **and never served without a tenant check**". Note also that
  removing the mount breaks every writer that returned a `/uploads/...` URL — three of them here — so
  the step should name converting the write paths as part of its scope.

- [INFO][SPEC-002 Step 11 — A 403 ON A FOREIGN OBJECT IS ITSELF A DISCLOSURE] Worth one line beside
  A14: an authorisation failure on someone else's storage key must be a **404**, not a 403. A 403
  confirms the object exists, which converts "may I read this?" into "does this exist?" and is enough
  to enumerate another tenant's documents given a key. The existing-but-foreign and never-existed
  cases should be indistinguishable.

- [BUG][SPEC-002 Step 12 / A17 — AUTHENTICATION CANNOT READ `memberships` THROUGH THE ORM, AND THE
  SPEC DOES NOT SAY HOW] A17 requires that revoking a membership denies access on the next request,
  which means session lookup must re-read `memberships` every time. But `Membership` is `TenantOwned`,
  so an ORM query invokes the §4.4 filter — which demands an account context that authentication runs
  *before*: resolving the session is how the account gets chosen. Implemented literally, every
  authenticated request raises `LookupError`.

  Three ways out, and the spec picks none: bind a context (circular), use `skip_tenant` (N9 forbids
  putting the codebase's `sudo` on the hot path of every request), or read via a **Core select** so no
  mappers are involved and the filter correctly skips it — with RLS's `membership_self` policy (A10)
  providing the real boundary. **Fix:** §4.4 or Step 12 should state that the auth path reads
  `memberships` without the ORM filter and that `membership_self` is what secures it. This is the same
  bootstrap tension A10 already solves for RLS, unsolved one layer up.

- [INFO][SPEC-002 Step 12 — TWO PROPERTIES A16 IMPLIES BUT DOES NOT NAME] A16 lists the cookie flags.
  Two adjacent requirements are easy to miss and both are security-relevant: (a) the session id must
  **rotate on sign-in** and the old row be deleted, or a fixated cookie stays authenticated; (b) the
  `Secure` decision must come from the **request host**, not a debug flag — a flag can be wrong in
  production, and it must be dropped on loopback because browsers refuse Secure cookies over http, so
  development cannot sign in otherwise. Worth adding beside A16 since both are one line of code and
  invisible when absent.

- [BUG][SPEC-002 Step 14 / D14 — THE RPO CHECK STEP 14 ASKS FOR HAS NO API TO CALL] Step 14 wants
  `doctor` to check that "the managed provider's most recent backup is within the RPO window." D13
  leaves the vendor as "an implementation detail," so nothing in the codebase can name which API to
  query, and no credentials for one are configured anywhere. Built instead: a check against **our
  own** media backups' mtime (`create_backup`'s output), with the RPO taken as 24h from D14's
  "automated daily backups" until a real provider SLA sets a number. **Fix:** once a vendor is
  actually selected and its SLA recorded (`MULTITENANCY.md` §11.1), Step 14 should name that
  provider's status API and the credential it needs, the same way A17 needed the bootstrap
  exception named rather than implied.

- [DEFER][SPEC-002 Step 14 — THE BACKUP ARCHIVE ITSELF IS NOT DURABLE ON A HOSTED DEPLOYMENT] `mihomes
  backup` now round-trips media correctly through `StorageProvider` (G14.2), but the **archive file**
  it produces still lands on local disk (`BACKUPS_DIR`). On a Fly machine with no persistent volume
  (§11.3 already rules those out for exactly this reason) that file does not survive a redeploy.
  Two real options, both out of scope for G14: enable versioning on the bucket the live objects
  already sit in and let `mihomes backup` become a smoke test rather than a copy, or add a genuinely
  separate `S3_BACKUP_BUCKET` and push the archive there. Building the second option against a bucket
  nobody has provisioned would be exactly the "second unmonitored backup system" D13 warns against
  for the database half, just moved to the media half — so it waits for an operator decision, not a
  guess.

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

## SPEC-003 pre-flight (2026-08-18) — surfaced during G0

- [BUG][low] `src/mihomes/models/membership.py:77,95` — `MembershipPropertyScope` assigns
  `__table_args__` **twice**; the second binding wins, so `ix_scopes_account_membership` and
  `ix_scopes_account_property` were never created. Confirmed absent from `0001_pg_baseline.py`,
  which carries only `uq_scope_membership_property`. **Performance only, not an isolation hole** —
  `scoped_property_ids()` queries by `membership_id`, which the surviving unique constraint's
  leading column covers. Fix: merge the two tuples, then autogenerate the missing indexes.
  (surfaced during SPEC-003 G0 pre-flight)

- [OPT] `src/mihomes/models/` — a class-level lint for a second `__table_args__` assignment would
  make the above impossible to reintroduce. Six other model files declare it more than once, but
  each of those has more than one class in the file, so only `membership.py` is a real collision.
  A test that walks every mapper and asserts its declared constraints/indexes all appear in the
  live schema would catch this class of silent loss generally. (surfaced during SPEC-003 G0)

- [BUG][medium] SPEC-003 §4.4's `REDACTED_FIELDS` names **five fields that do not exist**:
  `WorkOrder.cost`, `WorkOrder.invoice_number`, `Asset.value`, `Consumable.last_order_cost`,
  `Task.estimated_cost` (Task has no money column at all — `estimated_hours` is Float hours). A
  frozenset of nonexistent names redacts nothing, silently, **and A12 still passes** because
  A12's scope is that same dict. Corrected in `build-loop-spec003.md` C8; closed by the G-exists
  and G-census gates rather than by editing the spec. (surfaced during SPEC-003 pre-flight)

- [BUG][medium] SPEC-003 §4.4 misses 9 of the 15 `Money` columns in the tree. **`Event.budget` is
  the sharp one**: §4.1 classifies `event` as property-scoped, so staff may see the row, and it
  carries money §4.4 never mentions — F4's exact shape, missed by the table written to close it.
  `Insurance` is money-bearing, property-scoped, and absent from §4.1 entirely. Corrected in
  `build-loop-spec003.md` C9. (surfaced during SPEC-003 pre-flight)

- [BUG][low] SPEC-003 §4.1's entity classification omits 13 mapped models (`insurance`, `alert`,
  `vendor_rating`, `staff_pto`, `ai_conversation`, `tag`, `template`, `property`, `account`,
  `membership`, `invite`, `audit_log`, `session`), which N4 explicitly forbids. Its *rationale*
  for the account-level class is also wrong: `budget`, `contract`, and `recurring_expense` all
  carry `property_id`, so "no property to scope by" is false for all three — the outcome
  (deny-for-staff) stands as a policy decision, the stated reason does not. Corrected in C10.
  (surfaced during SPEC-003 pre-flight)

- [OPT] `tests/conftest.py:360` — `web_client_factory` enters `account_context(account_a)` for the
  whole test, which is what hid C12 (no web request binds tenant context) through all of SPEC-002.
  Once every route carries `require_authenticated`, this ambient binding should be **removed** so
  web tests exercise the real binding path rather than the fixture's. Removing it before then
  would redden the whole web suite at once. (surfaced during SPEC-003 G0)

- [DEFER][SPEC-002 harness] `tasks/build-loop-spec002.md` never recorded that
  `LANDING_TEST_DATABASE_URL` must point at a **separate** database. Its reported
  `1562 passed, 0 failed` is only reproducible with it set; under a single database the same tree
  reports `4 failed, 1558 passed`. Documented in `build-loop-spec003.md` §0.2 rather than
  back-edited into a completed run's harness. (surfaced during SPEC-003 pre-flight)

## SPEC-003 G6 (2026-08-19) — action declarations on 142 routes

- [BUG][medium] **SPEC-003's 21-key action vocabulary does not cover four existing features.**
  Step 5 is mechanical declaration, but four router modules have no honest key to declare:
  - `staff.py` (7 routes) — these manage the **`Staff` HR model**, and F2d is explicit that row 10
    "Manage staff" governs **memberships, not HR data**. §4.1 classifies `staff` as `PERSONNEL`
    with the rule *"Staff may see their own record; never others'"* — **no matrix key expresses
    that**. Declared `member.manage` (staff denied outright), which is fail-closed but stricter
    than §4.1 describes: a housekeeper cannot see their own record.
  - `books.py` + `library.py` (4) — §4.1 classifies `book` `ACCOUNT_LEVEL` (denied to staff), but
    C10 showed that rationale is wrong for `Book` (it carries `property_id`) and the library is
    functionally inventory. Declared `inventory.manage`, i.e. **by function, against the
    classification**.
  - `templates_route.py` (4) + `playbooks_route.py` (3) — both models are `ACCOUNT_LEVEL`; both
    generate tasks. Declared `task.manage`. No key expresses "account-level operational config".
  **Fix:** either add keys in a Step-1 follow-up (`staff.view_own`, `library.manage`,
  `automation.manage`) or reconcile §4.1's classification with the declarations. Until then these
  four are the concrete instances of the residual §10 already admits — *"the harness proves every
  route declares something, not that it declared the right thing."* (surfaced during G6)

- [BUG][low] **Staff can write money they are not allowed to read.** `complete_work_order` writes
  `WorkOrder.actual_cost` and is declared `issue.manage`, which is `SCOPED` for staff — because
  declaring `finance.view` instead would deny staff the ability to complete work at all, which
  D14 explicitly rejects (*"redaction honours both without removing records staff need to do
  their jobs"*). So a housekeeper may set a cost that redaction then hides from them. D14 covers
  **reads**; the spec never addresses money **writes** by staff. Needs a product decision: either
  a cost-entry sub-permission, or accept it. (surfaced during G6)

- [OPT] The three price-entry route groups (`assets.add_price_entry`,
  `inventory.{add,edit,delete}_item_price`) were declared `finance.view` rather than
  `inventory.manage` precisely to avoid the above — `inventory.manage` is `SCOPED` for staff and
  would have handed price history to the role row 9 denies finances to. Worth a regression test
  at G7/G8 asserting no `Money`-writing route carries a staff-`SCOPED` action. (surfaced during G6)

- [DEFER][G15] `web/routes/ai.py` still returns *"Run `mihomes ai setup` in the CLI"* to the
  browser (`_AI_ERROR_HINT`, `:48-49` — §3 cites `:47-48`, line drift). §3 lists deleting it under
  this phase, but its replacement is Step 15's settings UI. Deleting it at G6 would leave a worse
  message than the wrong one; it moves with G15. (surfaced during G6.4)

## SPEC-003 G7 (2026-08-19) — query-layer scoping

- [BUG][low] **Child tables of property-scoped models carry no `property_id`, so a *direct* query
  on them is unscoped.** `PriceEntry`, `ConsumablePriceEntry`, `TaskSchedule`, `EventGuest`, and
  `Guest` are all `PROPERTY_SCOPED` by classification but have no column for
  `authz/query_scope.py` to filter on. Loading them **through their parent** is protected by the
  parent's filter; a query that starts at the child is not. The two price-entry models are
  additionally covered by field redaction (§4.4), so the live exposure is `TaskSchedule`,
  `EventGuest`, and `Guest` — none of which any current route queries directly. **Fix options:**
  scope via a parent join in the listener, or denormalise `property_id` onto the children. Worth
  a test that asserts no route reaches a child model without its parent. (surfaced during G7)

- [OPT] The scope listener adds a `with_loader_criteria` option per property-scoped model to
  **every** ORM statement in a staff request (currently 16 options). SQLAlchemy ignores the ones
  whose entity is absent, so it is correct, but it is more work per query than necessary.
  Restricting the options to entities actually present in the statement would cut that; measure
  before optimising, since the criteria are cheap to construct and this has not been profiled.
  (surfaced during G7)

- [OPT] `tests/conftest.py::web_client_factory` now signs its client in as an **owner** so the
  ~500 pre-existing web tests keep testing their own subject rather than becoming authorization
  tests. That is the right default, but it means those tests exercise exactly one role. The
  role-specific coverage lives in `tests/integration/test_query_scope.py` via `web_client_as`;
  worth reviewing at G17 whether any pre-existing web test *should* be parameterised across
  roles. (surfaced during G7)

## SPEC-003 G10 (2026-08-19) — AI scoping

- [BUG][high — FIXED IN G10] **`query_budget` returned the household's finances to staff.** The
  property-scope listener covers models classified `PROPERTY_SCOPED`; `Budget`, `Transaction`,
  `Contract`, and `InsurancePolicy` are `ACCOUNT_LEVEL`, so nothing filtered them on the AI path.
  The equivalent *web* route is denied by row 9 — **but the AI path has no route**, which is F3's
  point restated. Closed by an entity-class gate in `execute_tool` and a matching one in
  `assemble_context`. Recorded because it was live, not theoretical. (surfaced during G10)

- [BUG][high — FIXED IN G10] **The property-scope listener was armed only as a side effect of the
  web layer importing `authz.query_scope`.** Any other consumer — the Telegram bot's two DB
  paths, a CLI report, a background job — would bind a scope that **nothing read**, and fail open
  silently. Now armed from the `mihomes.authz` package root. (surfaced during G10)

- [BUG][medium] **N2 is not fully satisfied, and this is the honest statement of it.** §4.3/N2
  require the scope as a **required positional parameter** so *"a forgetting call site fails to
  import."* It is carried in a ContextVar instead, which defaults to `None` = unrestricted — so a
  *new* call site that never binds a scope fails **open**. Every current `execute_tool` call site
  is inside a request that binds the role, so there is no live exposure, but the footgun N2 names
  is preserved for future code. **Fix:** give `current_role`/`current_property_scope` a third
  `UNSET` state and refuse at the AI entry points when unset, forcing the CLI and background jobs
  to bind "unrestricted" explicitly. Deferred rather than done because it changes the CLI and
  smoke paths, which are not G10's subject. **G16 must bind a role explicitly for the bot** —
  D16 says an unlinked sender is staff-level, never unrestricted. (surfaced during G10)

- [OPT] `execute_tool` converts exceptions into a `"Tool error (…): …"` string that echoes the
  caller's input. A test asserting `except Exception` around it therefore catches nothing, and a
  naive `secret not in output` assertion fires on the echoed *input* rather than on leaked data —
  which cost a debugging cycle here. Worth a helper that distinguishes refusal from leakage, so
  future scoping tests do not re-learn it. (surfaced during G10)

## SPEC-003 G11 (2026-08-19) — onboarding

- [BUG][high — FIXED IN G11] **`0002_rls` read the *live* tenant registry, so adding any new
  tenant table broke the migration chain from scratch.** It generates policy DDL from
  `TENANT_TABLES` at *its* point in the chain; registering `onboarding_state` (created two
  revisions later by `0004`) made `0002` try to `ALTER TABLE` a table that did not exist yet.
  Surfaced as **82 errors** on the CLI test database, which is dropped and rebuilt by migrations
  every run — the main suite's `create_all()` path hid it completely.
  **A migration must be a fixed point in history**; one that reads code which keeps changing is
  not. Fixed by intersecting with the tables actually present, and by making each later migration
  apply RLS for the table *it* creates. Worth a general rule: no migration should import a mutable
  registry without pinning or filtering it. (surfaced during G11)

- [OPT] `tests/unit/test_tenancy_registry.py::test_each_tenant_table_has_account_id` required an
  explicit index on `account_id`; a **primary key** on `account_id` now also satisfies it.
  `onboarding_state` is keyed on `accounts.id`, so Postgres already indexes it and adding a second
  index would have made `alembic check` report drift. The invariant is unchanged — every tenant
  query filtering on `account_id` must hit an index — only *how* it can be satisfied is wider.
  (surfaced during G11)

- [DEFER][G11] **The onboarding *web wizard* is not built** — the service, model, migration and
  resumability logic are (A17/A18 green at the service layer), but `web/routes/onboarding.py` and
  its templates are not. They need a dependency that requires a signed-in **user without an
  account**, since `enforce_declared_action` resolves an account and would 403 steps 1–2. The
  route class for pre-account screens is a genuine gap in §4.1's `Access` vocabulary
  (`ITEM`/`COLLECTION`/`ACCOUNT` all assume an account). Deferred to keep G11's commit to one
  coherent change; the flow is fully testable and covered without it. (surfaced during G11)

## SPEC-003 G13 (2026-08-19) — account switcher

- [DEFER][web-layer] **Three groups now have a complete service layer and no UI**: the onboarding
  wizard (G11.4), the invite accept/manage screens (G12), and the switcher control (G13). They
  share one blocker — §4.1's `Access` vocabulary (`ITEM`/`COLLECTION`/`ACCOUNT`) has **no route
  class for a screen that runs before or across account selection**. Onboarding steps 1–2 have no
  account yet; invite acceptance happens before the invitee is a member of anything; switching
  targets an account other than the current one. `enforce_declared_action` resolves an account and
  would 403 all three.
  **Resolve the vocabulary once, then wire all three** — inventing a fourth `Access` value
  separately in three groups is how the two halves drift. Candidate: `Access.SESSION` for routes
  authorised by a signed-in *user* rather than by a role within an account, enforced by
  `require_authenticated`-without-account. (surfaced during G13)

- [OPT] `users.last_used_account_id` is written on every successful switch but **nothing reads it
  yet** — the sign-in path (`web/routes/auth.py`) still leaves `current_account_id` unset for a
  new session. Wiring it is a two-line change in the callback and belongs with the web-layer work
  above; until then D11's "persists last_used_account" is half-implemented: it persists, and
  nothing resumes from it. (surfaced during G13)

## SPEC-003 G16 (2026-08-20) — Telegram bot scoping

- [OPT] **F5's cited line reference is stale.** The harness (and SPEC-003 §6 Step 16) name
  `review.py:120 _build_estate_context` as the second DB path that must be scoped. That function
  no longer lives there — it is `review_common.build_estate_context`, reached from both
  `review.py` and the responder. Both paths *are* scoped (G16.4 verifies it), but the spec's
  pointer would send the next reader to the wrong file. Worth a line-reference sweep across §6
  before the report: three of the citations checked so far have drifted.

- [PATTERN] **Third instance: a migration that imports mutable application state is not a fixed
  point in history.** `0001_pg_baseline` built its drift-guard triggers from live
  `Base.metadata`, so adding `telegram_links` in `0007` made the *baseline* migration try to
  create a trigger on a table that would not exist for six more revisions — `UndefinedTable`,
  raised from a migration nobody touched. Same shape as `0002_rls` reading live `TENANT_TABLES`
  (fixed in G11) and `0004` creating an index the model does not declare.
  **Fix applied:** `trigger_ddl_statements(metadata, only_tables=...)`, with `0001` scoped to the
  tables present at its own point in the chain and `0007` emitting its own policy and trigger.
  **The general rule** — already in `lessons.md` — is that a migration may read application
  *code* but never application *state that later migrations change*. Three instances in one spec
  suggests the guard should be mechanical: a test that runs `upgrade` one revision at a time and
  fails if any revision's DDL references a table not yet created. Cheap, and it would have caught
  all three at the revision that introduced them rather than six revisions later.

- [OPT] **Two SPEC-002 gates needed declared exceptions for a legitimately-different table**, and
  both were the right shape to absorb it (a dict keyed by name, one written reason per entry):
  `ix_telegram_links_lookup` cannot lead with `account_id` because sender resolution is *how* the
  account is discovered, and `telegram_links.telegram_user_id` is an integer because Telegram
  minted it. The second required a new bucket, `FOREIGN_SYSTEM_IDS`, distinguishing ids owned by
  *other* systems from one of ours left behind — the case the catch-all was written for. No rule
  was loosened. Noting it because three consecutive groups (G11, G16 x2) have had a pre-existing
  gate correctly refuse a new model: the fail-closed-on-new-model discipline is paying for itself.

## SPEC-003 G17 (2026-08-20) — the cross-cutting leak matrix

- [BUG][high] **FIXED — `/library/` returned another property's books to scoped staff.**
  `Book` was `ACCOUNT_LEVEL` (which has no query-layer enforcement) behind `library.py`'s
  `inventory.manage` declaration (which row 7 grants staff as `SCOPED`). The route is the
  *all-properties* book listing, so it has no per-property filter of its own by design and relied
  entirely on the query layer — which `ACCOUNT_LEVEL` never engages. **Fix:** reclassified `Book`
  to `PROPERTY_SCOPED`, which is what the data (`Book.property_id`) and the route declaration
  already agreed on; C10 had recorded §4.1's rationale as wrong here. No route change. Verified by
  mutation: reverting the classification turns `test_library_scoped_for_staff` red.

- [BUG][high] **FIXED — `/ai/sessions/{id}` returned an owner's saved AI answer to scoped staff.**
  Four `/ai/sessions*` routes declared `ai.use`, which row 18 grants staff as `SCOPED`. But
  reading a stored transcript is not "using the assistant": `AIConversation` is `ACCOUNT_LEVEL`,
  holds every answer including owner-only financial ones, and **carries no author column at all**
  (`role` is the AI persona — `financial`, `estate_manager` — not the member who asked), so there
  is nothing to scope by even in principle.
  **This is the leak G10 structurally could not see.** G10 scoped the *live* path and proved a
  staff member asking about another property gets nothing. The transcript of a question an *owner*
  already asked is a stored row on a different route. Two surfaces onto the same data, one scoped.
  **Fix:** the four transcript routes now declare `audit.view` (row 17, account-level, denied to
  staff) with `Access.ACCOUNT`. `/ai/` and `/ai/ask` keep `ai.use` — denying staff the assistant
  to fix this would break a capability the matrix grants, the over-correction `/library/` avoided.
  `audit.view` is **approximate**: the right answer is a `transcript.view` key or an author column
  so a member can read their own history. Fifth instance of the G6 approximate-declaration class.

- [FIXED 2026-08-20][BUG][medium] **`/ai/` and `/ai/sessions-panel` returned HTTP 500 for every
  role, including owners.** `_list_sessions` (`web/routes/ai.py:88`) called
  `func.min(AIConversation.id)` and Postgres has no `min(uuid)`. The column became UUID in
  SPEC-002 G6.1 and this aggregate was never revisited — so the AI page's session history had
  been dead since that conversion, on every role, and nothing caught it: the AI tests mock
  `get_ai_api_key` and never render the page.
  **Fixed** by grouping on `min(created_at)`/`max(created_at)` and joining back on
  `(session_id, first_at)`. That is not a substitution: `created_at` carried the ordering intent
  all along, and `min(id)` only expressed it *incidentally* while ids were sequential integers.
  Regression coverage in `tests/integration/test_ai_sessions.py` — including the assertion whose
  absence let it live, somebody asking for a 200.

- [PATTERN] **Only ONE of the six entity classes is read by any code.** Grep `EntityClass.<VALUE>`
  across `src/`: `PROPERTY_SCOPED` is read by `query_scope.scoped_models()`, `FLAGGED` and
  `PROPERTY_LINKED` are reached by *model name* rather than by class, and `ACCOUNT_LEVEL`,
  `PERSONNEL` and `GLOBAL` are read by **nothing at all**. Where those three are enforced, it is
  by whatever action the *route* happens to declare — two independent statements that nothing
  compared until this group. **Both G17 leaks are that gap**, and both were `ACCOUNT_LEVEL`, the
  class where "I classified it, so it's protected" is most tempting and least true. Worth a
  design pass in Phase 3: either give the unenforced classes a mechanism, or rename them so they
  read as documentation rather than as controls.

- [OPT] **U6 — no class fits `Template`/`TemplateItem`.** C10 classified them `ACCOUNT_LEVEL`, but
  §4.1's own account-level list is `budget/contract/recurring_expense/transaction/configuration/
  note/book` and does not contain `template` — so there is no source authority behind it.
  Meanwhile row 5 (`task.manage`) is `SCOPED` for staff and creating tasks from a template *is*
  task management, so `templates_route` declares `task.manage` and staff reach the rows.
  Enforcing `ACCOUNT_LEVEL` would break a capability the matrix grants. The sensitivity argument
  runs the same way: a template's fields are name, description and checklist items — the same
  class of content as a `Task`, which staff already see. What is missing is a class for
  *"account-wide, not sensitive, staff use it"*. Declared in `NO_CLASS_FITS`, not silently left.

- [DEFER] **Five property-scoped child tables are reachable by out-of-scope staff** — now
  *proven*, not assumed. `PriceEntry`, `ConsumablePriceEntry`, `TaskSchedule`, `EventGuest`,
  `Guest` are `PROPERTY_SCOPED` but carry no `property_id`, so `scoped_models()` skips them; a
  query through the parent is protected, a direct query is not. `Guest` is the sharpest: a guest's
  name is not money, so unlike the two price tables **no redaction covers it** — a scoped staff
  member can enumerate the names of people invited to another property's event. Pinned in
  `NOT_YET_ENFORCEABLE` with a reason each and asserted as currently-true, so the day child tables
  get a scoping path the entries turn red instead of quietly outliving the problem.

- [PATTERN] **The derived gate caught its own author.** `test_property_scoped_models_are_enforced_
  or_declared` builds its model list from `ENTITY_CLASSES` rather than from a written list, and
  immediately turned red naming `ConsumablePriceEntry` and `EventGuest` — two models missing from
  the first draft of my own exception dict. A hand-written matrix would have been green and wrong.
  Fourth group running where a derived-rather-than-transcribed gate found something (G2's census,
  G11's registry, G16's two, now this).

---

## SPEC-003 U-gate closure — 2026-08-21

The four items the SPEC-003 report carried forward (U1, U6, U7, and the `/ai/` 500) are closed.
What follows is what closing them turned up, which is more interesting than the closures.

- [RESOLVED] **U1 — secrets are encrypted at rest.** O1 is answered: Fernet, keyed from
  `MIHOMES_SECRET_KEY`, values stored with a versioned `enc:v1:` prefix so the format is
  self-describing and rotation does not have to guess. **`list_config` was the participant that
  would have been missed** — it runs its own `session.query(Configuration).all()` rather than going
  through `_lookup`, so a decrypt shim placed only in `get_config` leaves the settings page and
  `mihomes config list` rendering base64. Three existing tests failed, which is how it was found.
  Conversion of legacy rows is a command (`mihomes config encrypt-secrets`), deliberately not a
  migration: a migration that reads the encryption key depends on the environment it runs in, and
  this phase hit that trap three times. The assertion that proves it: a raw `SELECT value` shows
  `enc:v1:` and no plaintext — everything else would pass against a no-op cipher.
  **Also fixed a pre-existing CLI leak**: `config set` echoed the value unmasked and took it
  positionally, so a bot token landed in both scrollback and shell history.

- [RESOLVED] **U7 — the unenforced classes have a mechanism, and it closed two more leaks.** The
  `[PATTERN]` entry above ("only ONE of the six entity classes is read by any code") is now acted
  on rather than noted. `ACCOUNT_LEVEL` and `PERSONNEL` are derived from the classification and
  filtered at the query layer. Two live leaks fell out while building it, both reproduced through
  HTTP first: **`/search/` returned notes from properties a staff member cannot see**, and
  **`/vendors/` rendered the vendor ratings D12 denies staff by name**. Neither was a route
  mistake — both routes correctly declare actions staff hold, and both read an `ACCOUNT_LEVEL`
  model from inside a *service*, which is precisely the residual G17's endpoint-source scan
  admitted. Four leaks now trace to that one gap.

- [PATTERN] **A comment asserted a classification enforced something, in the file whose job is
  enforcement.** `authz/redact.py` said, in prose: *"`VendorRating` is classified `ACCOUNT_LEVEL`,
  so staff never receive the row."* False when written, and it read as authoritative *because* of
  where it was. The classification was right; nothing read it. This is the sharpest form of the
  documentation-drift problem this file keeps recording: not a comment that went stale, but one
  that was never true, stating a mechanism rather than an intention. Worth a habit — when a comment
  claims something is enforced, name the function that enforces it, so the claim is checkable.
  `test_the_enforced_classes_name_the_code_that_enforces_them` now does that mechanically for the
  two classes U7 added.

- [PATTERN] **`.count()` escapes a `do_orm_execute` filter, and the same one-line mistake is
  correct in one file and wrong in another.** `state.all_mappers` is **empty** for
  `session.query(Model).count()` and `select(func.count()).select_from(Model)` — the statement's
  top-level column is a bare `count(*)`, not an entity — so gating a listener on it silently skips
  every count. Measured, both layers.
  In `authz/query_scope.py` it was load-bearing and is fixed (a tree walk over the statement's
  tables). In `tenancy/session.py` it is **deliberately left alone**, for two reasons each
  established by measurement: RLS catches it (probed as the non-superuser role production connects
  as, every count shape returns the correct number — the alarming 8-vs-2 figure came from a
  *superuser* probe, and superusers bypass RLS unconditionally even under FORCE ROW LEVEL
  SECURITY); and widening it turns 44 tests red, because `auth/sessions.py` resolves a membership
  with a Core `select(memberships.c.role, ...)` **before any account context exists** and its
  docstring names the empty `all_mappers` as the mechanism that lets it through, while N9 forbids
  `skip_tenant` on the hot path of every request.
  The stateable difference: **RLS enforces the account boundary; nothing enforces the property
  boundary except `query_scope`.** A uniform fix would have removed a documented bootstrap path to
  add redundancy behind a stronger enforcer. Residual, not defect.

- [PATTERN] **Superuser probes cannot distinguish "defence-in-depth gap" from "live leak", and I
  nearly reported the wrong one.** The first `.count()` probe connected as `postgres` and showed a
  cross-tenant count of 8 where `.all()` gave 2 — which looks exactly like a live multi-tenancy
  leak. Re-probed as `mihomes_test_app`: every shape correct. `tests/conftest.py`'s `app_engine`
  docstring already warns about this in writing, which is the point — the warning existed and I
  still had to be reminded of it by a second measurement. **Any claim about tenant isolation must
  name the role the probe connected as.**

- [PATTERN] **Two mutation checks with no teeth, for two different reasons — and the harness
  itself was the first bug.** Its initial version reported "0 failed" for every mutation including
  deleting an arm outright: pytest emits ANSI colour, so `line.startswith("FAILED")` never matched.
  *A mutation harness that cannot see failures reports perfect safety.* Once fixed, two of eight
  arms still came back with no teeth, and the diagnoses were opposite: `_property_id_criteria`'s
  `is_not(None)` guard was genuinely **redundant** (`NULL IN (...)` is NULL, and a WHERE clause
  keeps only rows evaluating to *true*, so the row is excluded either way — measured with three
  seeded ratings) and was removed; the no-linkage `false()` branch was **real but untested**
  (probing showed it correctly suppressing a `Tag` row) and got the test it was missing. An
  unnecessary condition in a security filter is worse than none — it reads as a handled hazard.

- [PATTERN] **A test that asserts correctly while measuring the wrong layer.** The first version of
  the count-vector test sat on the app-role connection, where RLS answers the count correctly no
  matter what the ORM does — so it passed with the bug fully present. Moving it to the superuser
  connection *inverted this file's central rule for one specific assertion*, and that inversion is
  the reason it works. Both versions assert a true thing; only one can fail when the code breaks.

- [RESOLVED] **Cross-test pollution that looked intermittent needed both defects to bite.**
  `test_archive.py::TestGetStats::test_counts_eligible_rows` had been failing in full-suite runs
  and passing in isolation. Cause: `audit_deny` writes on an independent session and commits —
  correct A33 behaviour, since a deny audited through the request session is discarded by the
  rollback that reports it — so every route test provoking a 403 leaves a committed row
  `web_client_as`'s rollback cannot reach. `test_leak_matrix.py`'s two denials were still visible
  ~380 tests later. Those rows belong to *other accounts* and would have been invisible if
  `.count()` honoured the tenant filter. **Neither defect alone was visible**, which is what made
  it look flaky. `web_client_as` now cleans its own account's audit rows on teardown.

- [RESOLVED] **U6 — `staff.view_own` and `automation.manage`.** Row 10 and row 5 split on the
  row-8 precedent. The ordering was forced and worth recording: U6a's `staff.user_id` had to land
  first (*"their own record"* needs a hard answer to which row is mine, and `Staff.email` is
  nullable, non-unique, and often not the sign-in address), then U7's `PERSONNEL` mechanism, and
  only then the key — because a grant that opens a route without a filter behind it is a leak, not
  a fix.

- [PATTERN] **Opening a route exposed a leak that only a paired assertion could catch.** Declaring
  `staff.view_own` turned `/staff/` into a full directory read: `web/deps.py` binds a property
  scope **only when the grant is `SCOPED`**, so an `ALLOW` grant leaves it `None`, and
  `_apply_property_scope` returns early on `None` as "unrestricted" — skipping the `PERSONNEL`
  filter for exactly the request it exists to constrain. Property scope and role are *different
  conditions* and conflating them was the bug. Caught on the first run by the test written
  alongside the reachability one; a test asserting only "the page returns 200" would have shipped
  it.

- [CONFIRMED, NOT RESOLVED] **`NO_CLASS_FITS["Template"]` survives U6b, and its original
  diagnosis was right.** The plan was that a dedicated matrix key would let the rows be denied at
  the query layer — "U6 resolves U6". Writing it showed that to be self-contradictory:
  `template_service.run_template` resolves the template *by slug*, so running one requires reading
  the row, and denying rows leaves staff a `/run` endpoint whose targets they cannot see. So the
  split is by **verb, not by row** — `automation.manage` governs create/delete, listing and running
  stay `task.manage` — and enforcement lives in the route declarations, because a verb distinction
  is not something a query layer can express. The entry's own words still name the real fix:
  *"what is missing is a class for 'account-wide, not sensitive, staff use it'."* A seventh §4.1
  class is spec work. U6b removed the unintended **write** access, which was the part that
  mattered.

- [PATTERN] **Three plan steps were wrong, and each was only visible once written.** "Retire the
  Template exemption" (contradicted by `run_template`'s slug lookup), "fix the `.count()` gap
  everywhere" (breaks the auth bootstrap), "the `is_not(None)` guard is load-bearing" (it changed
  no result). All three came from a plan written after real exploration, and all three survived
  until implementation forced the detail. The lesson is not "plan less" — the plan was right about
  the shape of every commit — it is that a plan's *justifications* deserve the same suspicion as
  its conclusions, and that mutation testing plus one honest probe is what converts a plausible
  justification into a checked one.

- [RESOLVED] **U6's last item — `EntityClass.ACCOUNT_SHARED`, the seventh class (2026-08-24).**
  `NO_CLASS_FITS["Template"]` is now empty, and its own text named the fix from the beginning:
  *"what is missing is a class for 'account-wide, not sensitive, staff use it'."* Templates are
  account-wide (no `property_id`), staff legitimately **use** them (row 5 grants running one, and
  `run_template` resolves by slug so running requires reading the row), and their fields are the
  same class of content as the Tasks they generate. Both models reclassified; the two
  `_ACCOUNT_LEVEL_EXEMPT` entries that existed only to neutralise the wrong label are gone.

- [PATTERN] **A wrong classification and a missing enforcement look identical from inside an
  exemption list.** U7's finding was "four classes are enforced by nothing" and its fix was to give
  them mechanisms. `Template` looked like the same problem and was not: it was *correctly*
  unfiltered and *incorrectly* labelled, so every attempt to enforce its class broke a capability
  the matrix deliberately granted. Two plans failed on that before the third worked — G17 recorded
  the gap, U6b tried a matrix key and confirmed the gap instead. **Rule:** when a model has to be
  exempted from its own class's enforcement, ask whether the class is wrong *before* asking how to
  enforce it. An exemption list holding only structural "the filter would be circular here" cases
  stays small; one that also absorbs "the class is wrong here" grows, because each new entry has a
  precedent that looks exactly like it.

- [PATTERN] **"No filter" must be declared, not merely absent.** The seventh class applies no row
  filtering — which is what the four classes U7 fixed also did, for a whole phase, by accident. A
  correct non-decision and a forgotten one are byte-identical in code. So
  `query_scope.UNFILTERED_CLASSES` states each unfiltered class with its reason, and a derived test
  asserts that *every* class is either filtered or declared — a new class fails the suite rather
  than silently joining the unenforced group. The declaration is the only thing separating "we
  decided" from "nobody noticed".

- [PATTERN] **A mutation with no teeth is not always a missing test.** Adding `ACCOUNT_SHARED` to
  `_governed_tables()` left all 69 tests green. Probing showed why: that set decides only which
  statements short-circuit *early*, and since no criteria is built for the class either way, no
  row's visibility changes — the statement just does slightly more work to reach the same answer.
  Behaviourally inert, so writing a test for it would have been testing an implementation detail
  and pinning a decision that has no consequence. Recorded in the docstring instead, with the
  measurement, so the next reader does not "fix" the omission. **Third distinct diagnosis for a
  toothless mutation this month** — redundant condition (delete it), untested arm (add the test),
  and now inert difference (document it). The reflex to add a test for every surviving mutation is
  wrong a third of the time.


## SPEC-004 (Phase 3, Billing/Freemium) — 2026-08-25

- [DEFER][U8] `services/metering/ai_wrapper.py::_check` — **a metering-infrastructure outage lifts
  the AI ceiling for its duration.** The ceiling check fails *open* when the lookup itself raises.
  Measured before choosing: the AI route reads its provider key from the database before a provider
  exists, so a dead database already fails the request — the real choice was between a confusing
  billing error and a database error, not between capped and uncapped. A `Denied` still raises;
  only the lookup is wrapped, and re-mutating the ceiling confirmed A14 kept its teeth. Bounded and
  accepted; recorded so it is not rediscovered as a bug.

- [OPT] `services/metering/meter.py` — **two sessions per AI call.** `_check` opens one and
  `_record` another, so the count read can be stale by the time the increment lands. The same
  window the no-reserve decision already accepts (see `check_and_reserve`'s docstring), and closing
  it would mean building the reservation that function deliberately is not. Not acted on.

- [OPT] `services/vendor.py:3,5` — two unused imports (`date`, `func`) that predate this phase,
  confirmed present at `4178286`. Left alone: they are outside SPEC-004's diff, and the tree-wide
  ruff backlog (~448 findings) is its own task on its own branch.

- [BUG][low] `PRICING` §4.3's **home-picker is not built.** §4.3 promises *"an in-app picker shown
  from day 0 of Grace"* so the owner chooses which home stays active. `restricted.restriction_for`
  takes `chosen_ids` and honours it — the mechanism is there — but no UI writes it, so today every
  restricted account gets the oldest-created default. Phase 4 UI work; the service needs no change.

- [BUG][low] **`trial_ending` is counted but not sent.** `cli/jobs.py::trial_sweep` counts trials
  ending within the window and `EmailService.send_trial_ending` renders correctly (A21), but the
  sweep does not call it. Split deliberately so a failure in the mail path cannot mark a trial as
  notified without notifying anyone — the wiring, and the "sent once per cycle" marker it needs,
  are the remaining piece.

- [DEFER][O1] The **four `STRIPE_PRICE_*` env vars, `STRIPE_SECRET_KEY` and
  `STRIPE_WEBHOOK_SECRET`** do not exist in any environment, and no Stripe account is configured.
  Every criterion is proved against `FakeBillingProvider`; nothing here proves the live account's
  own configuration. Launch prerequisite, founder-owned.

- [DEFER][D15] **No scheduler runs the two jobs.** `mihomes jobs trial-sweep | reconcile` are
  idempotent and safe to run twice — the half that is provable without one — but Fly's scheduled-
  machine mechanism has **not** been verified against their documentation, so the deployment shape
  remains a default with a named alternative rather than an asserted fact.

- [DEFER][SPEC-005 U11] **~138 `except Exception` blocks outside the request path.** A32 is
  scoped to `src/mihomes/web/` by C3 — 18 handlers, all of which now log or re-raise — because
  scoping it to the whole tree would turn one acceptance criterion into a 154-site refactor
  wearing its name. The remainder is real cleanup with no criterion attached.

  **What G15 learned, and why the remainder is worth doing:** all nine offenders found in `web/`
  were *not* the `except Exception: pass` shape anybody greps for. Each set a user-facing error
  string and told the operator nothing — an AI call degrading to "AI isn't configured", a weather
  forecast falling back to `None`. Those are not crashes, which is why a hardening pass looking
  for crashes left every one of them.

  The check that found them is reusable and cheap: an AST walk asking whether each
  `ExceptHandler` for `Exception` **either logs or re-raises**, in
  `tests/unit/test_errors.py::_swallows_silently`. Pointing it at `src/` rather than
  `src/mihomes/web/` enumerates the remaining set in about a second.

- [DEFER][SPEC-005 §10] **Observability is instrumentation, not alerting.** Step 15 makes the
  system legible — one `dictConfig`, JSON in production, structured records, a request id on
  every request and response, real exception handlers, `/healthz` on the product app. **Nobody is
  paged.** `sentry-sdk` is config-gated and unconfigured, `PRD_REVIEW` E4 asked which monitoring
  stack and no doc has answered. At GA someone still has to be watching.
