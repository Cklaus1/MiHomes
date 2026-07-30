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
