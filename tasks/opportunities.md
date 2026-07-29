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

## New bugs (candidate tasks for next loop)
<!-- format: `- [BUG][proposed-severity] file:line — title — concrete failure — proposed fix (surfaced during <task-id>)` -->

## Deferred features / larger refactors (seeded by Step 2 decisions)
<!-- Decided out of this hardening run; candidates for the next loop. -->
- [DEFER][Q6] `services/ai/agent.py`, `provider.py` — provider-agnostic tool loop (`stream_with_tools()` capability on the provider protocol) so non-Claude providers get the full tool path instead of the degraded no-tools fallback (M36). Top candidate next run — architecture change, no P0–P2 depends on it.
- [DEFER][Q3] `web/routes/reports.py` (deleted this run) — if a reports page is wanted, rebuild against the live schema + mount + nav. Do NOT resurrect the dead module.
- [DEFER][Q5] `models/ha_entity.py` (deleted this run) — Home-Assistant entity sync is a future feature; re-add model + migration + integration when HA is in scope.

## Deferred/blocked from this run
<!-- format: `- [BLOCKED] <task-id> — why blocked, what would unblock it` -->
