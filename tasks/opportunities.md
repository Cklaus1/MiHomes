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

## Optimizations (captured, not acted on) — added during G-CLI
- [OPT] models/ai_conversation.py:19 + services/ai/orchestrator.py:34 + services/archive.py:113 — dead `tokens_used` field (never populated; only copied by archive). Dropping it needs a migration for marginal gain → deferred rather than fixed in the L10 hygiene pass (minimal-impact). (surfaced during G-CLI/L10)

## Deferred features / larger refactors (seeded by Step 2 decisions)
<!-- Decided out of this hardening run; candidates for the next loop. -->
- [DEFER][Q6] `services/ai/agent.py`, `provider.py` — provider-agnostic tool loop (`stream_with_tools()` capability on the provider protocol) so non-Claude providers get the full tool path instead of the degraded no-tools fallback (M36). Top candidate next run — architecture change, no P0–P2 depends on it.
- [DEFER][Q3] `web/routes/reports.py` (deleted this run) — if a reports page is wanted, rebuild against the live schema + mount + nav. Do NOT resurrect the dead module.
- [DEFER][Q5] `models/ha_entity.py` (deleted this run) — Home-Assistant entity sync is a future feature; re-add model + migration + integration when HA is in scope.

## Deferred/blocked from this run
<!-- format: `- [BLOCKED] <task-id> — why blocked, what would unblock it` -->
