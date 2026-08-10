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
