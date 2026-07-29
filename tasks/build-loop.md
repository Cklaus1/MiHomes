# MiHomes Hardening — Autonomous Build Loop

> **Input spec:** `tasks/hardening-spec.md` (v2, adversarially verified) — the source of truth for *what* to fix.
> **This file:** the harness — *how* to execute the spec autonomously to completion, resumably, without intermediate human review.
> **Branch:** `hardening-build` (NOT `main`). **Baseline:** `f172a2c`.
> **Invocation:** `/loop tasks/build-loop.md`

---

## 0. Prime directive & compound stop condition

Work the task DAG (§4) until the **compound stop condition** holds:

> **STOP when — and only when — (A) every checkbox in §4 is `[x]`, AND (B) every finding in `tasks/hardening-spec.md` is either landed-with-passing-test or explicitly logged as deferred in `opportunities.md`, AND (C) the full test suite is green, AND (D) the R1 smoke test passes.** All four. A green suite with unchecked tasks is NOT done. A full DAG with a red suite is NOT done.

Neither condition alone terminates the loop. This prevents the two classic failure modes: declaring victory because tests pass (but work remains) and declaring victory because tasks are checked (but the suite regressed).

**No intermediate review stops.** Do not pause to ask for approval between tasks or groups. The spec is already reviewed and its open questions are resolved (spec §"Resolved decisions"). Genuinely new blocking questions go to `opportunities.md` and the task is deferred — the loop continues with the next unblocked task.

---

## 1. The loop hierarchy

Five nested loops. Inner is per-task; each outer layer widens the blast-radius check.

### 1.1 Inner loop — one task to green (max 3 attempts = poison ceiling)
For the current task `T`:
1. **Write the failing test first.** Reproduce the bug the spec describes (the exact `file:line` + failure mode). For the silent-failure class (R1), the test must fail *because the error is swallowed* before the fix — assert on the observable wrong result, not on a log line.
2. **Apply the minimal fix** from the spec (with the ⟲ADV corrections — always use the corrected fix, never the struck-through original).
3. **Run `T`'s test + any test named in `T`'s `verify:` line.** Red → go to 4. Green → go to 5.
4. **Diagnose & retry.** Increment `T`'s attempt counter. On attempt 3 failing → **poison-task**: revert `T`'s changes (`git checkout -- <touched>`), mark `T` `[!]` (poisoned) in §4, append a `[BLOCKED]` line to `opportunities.md` with the failure, and move on. Never let one task stall the run.
5. **Regression gate (§1.2).**

### 1.2 Outer loop — no collateral damage
After `T` is individually green, run the **affected-area suite** — the test modules for `T`'s spec group (e.g. a Services task runs `tests/services/`, a gateway task runs `tests/gateways/`). Any *new* red that was green at the group's start = **`T`'s fix broke something** → treat as inner-loop failure (back to 1.1.4), consult the spec's **FIX-BREAKS** notes for `T` (many are pre-documented: D6, H13, H17, H21, H33, M21, M40). Fix within `T` or split out a sequenced sibling task.

### 1.3 Meta loop — per-group commit + checkbox (resumability boundary)
When every task in a DAG **group** (G1…Gn, §4) is `[x]` or `[!]`:
1. Run the **full suite** (§1.4).
2. Green → **commit** with message `hardening(G#): <group title> — <N tasks, M tests added>` and tick the group's header checkbox in §4 **and** the mirror in `todo.md`.
3. **The commit is the resume point.** On restart, the loop reads §4: the last group whose header is `[x]` and whose commit exists = done; resume at the first `[ ]` task of the first incomplete group. Checkboxes + commits are the *only* state — no external run log needed.

### 1.4 Full-suite regression loop
Before every group commit and once at the very end: run the **entire** test suite (`pytest -q`). This is distinct from §1.2's affected-area run — a Data-layer migration (R4) can break Web tests three groups away. Full green is a precondition for the group commit and for the compound stop.

### 1.5 Smoke loop (R1's teeth)
The R1 group ships a smoke test that **invokes every AI tool executor and every report section once against a seeded DB** (spec R1). After R1 lands, this smoke test runs as part of every full-suite pass. It is the safety net that would have caught D3/D4/M44 on day one; condition (D) of the stop requires it green.

---

## 2. The R4 extra-gate (migrations are special)

R4 is a schema/migration group and gets an **extra gate** beyond the normal loop, because a bad migration corrupts data irreversibly and `Float`→int-cents (R5) rides on it:

- **G-R4a (pre-flight):** on a *copy* of a seeded, populated DB (not `:memory:`), run `alembic upgrade head` then `alembic downgrade -1` then `upgrade head` again. Must round-trip clean. This catches H5-class broken downgrades and the `foreign_keys=ON`-during-migration hazard (spec R4 note a).
- **G-R4b (FK-enforcement order):** assert orphan-cleanup runs *before* FK creation within the same migration (spec H1) — test by seeding a deliberate orphan row and confirming the migration cleans-then-constrains without `IntegrityError`.
- **G-R4c (data-preservation):** seed known money values as `Float`, run the R5 cast migration, assert every value survives as exact integer cents (`CAST(ROUND(amount*100) AS INTEGER)` — no lost pennies). Assert enum defaults normalized to **names**, existing rows readable (spec M0/Q10).
- **G-R4d (autogenerate-clean):** after R4, `alembic revision --autogenerate` must produce an **empty** migration — proves models and schema finally agree (closes H1/H15-schema/M11–M15 drift). A non-empty diff = R4 incomplete.

R4 commits only when a–d all pass **and** the full suite is green. R5 (money TypeDecorator application) is gated behind R4 landing.

---

## 3. Poison-task ceiling & run-level circuit breaker

- **Per-task ceiling:** 3 attempts (§1.1.4), then poison `[!]` + defer. 
- **Run-level circuit breaker:** if **> 5 tasks** end poisoned, OR any **P0** task poisons, OR two consecutive *groups* fail their full-suite gate, **halt the loop** and write the end-of-run report (§5) with status `HALTED`. A cascade of poisons means the spec's assumptions are wrong somewhere upstream — pushing further just makes a mess. Halting ≠ failure; it's the harness refusing to thrash.
- A poisoned task never blocks its group's commit (the group commits with `[!]` tasks noted), but a poisoned **P0** trips the breaker immediately.

---

## 4. Task DAG

Grouped by the spec's cross-cutting refactors R1–R5 and P0 stop-the-bleeding, ordered by the spec's Suggested Sequencing. **Dependencies are top-to-bottom within a group and group-to-group as noted.** Each task: `id · spec-ref · one-line · verify:`. Tick `[x]` done, `[!]` poisoned.

Group headers carry the resume checkbox (§1.3).

### [ ] G0 — Stop-the-bleeding (P0 + P0-adjacent) — *no group deps*
- [ ] G0.1 · D5 · stamp `alembic_version` after demo `create_all` so `--demo`/`dev` boot · verify: `tests/web/test_demo_boot.py` (demo app starts, no OperationalError)
- [ ] G0.2 · D7+D8 · extract `watchdog_common.py`; POSIX-safe spawn, `proc.poll()` liveness (no zombie trust), Telegram backoff+gate, `with`-scoped log handle; fix zombie/`os.kill` in all 3 copies · verify: `tests/gateways/test_watchdog.py` (dead monitor detected; unconfigured telegram doesn't hot-loop)
- [ ] G0.3 · D6+H34+M45 · **one upload pass**: `read_document_upload` (images∪pdf, reject html/svg, None-filename guard, byte-cap, random name); relocate uploads to `MEDIA_DIR/"uploads"` centralized in `config.py`; `app.mount("/uploads",…)` with `nosniff`+`Content-Disposition: attachment`; route `save-report .md` through same path · verify: `tests/web/test_uploads.py` (svg rejected; pdf accepted; oversized rejected; **served with nosniff+attachment**; path under MEDIA_DIR not package)
- [ ] G0.4 · D1+Q7 · backup via `VACUUM INTO`/`.backup()` incl. checkpoint; restore disposes engine, deletes `db{,-wal,-shm}`, `extractall(filter="data")`, snapshots-first; **refuse restore while any pid file live** (`--force` override) · verify: `tests/services/test_backup.py` (WAL contents survive round-trip; restore refused with live pid; traversal archive rejected)
- [ ] G0.5 · D3+H10+M32+M33 · **one `tools.py` pass**: real enum values in schemas + query filters; bad status → error to model not dropped filter; aggregates honor filters (`summary_only` default path); `query_alerts` active-only · verify: `tests/ai/test_tools_status.py`
- [ ] G0.6 · M44+Q4 · rewrite `query_inventory` to query `Asset`+`Consumable` ORM (kill phantom `inventory_items` raw SQL) · verify: `tests/ai/test_query_inventory.py` (returns rows on seeded DB, no "no such table")
- [ ] G0.7 · H8+H9+Q1 · **atomic**: `DEFAULT_MODEL="claude-sonnet-5"` single constant; factory forwards `model=` all 4 branches; fix `reports.py:168,395`+`weather_tasks.py:77` call sites; remove `agent.py:41-42` workaround · verify: `tests/ai/test_provider_model.py` (configured model reaches provider + logged conversation)
- [ ] G0.8 · H11 · final no-tools call sends `tools=…, tool_choice={"type":"none"}` · verify: `tests/ai/test_agent_roundlimit.py`

### [ ] G-R4 — Reconciliation migration + env hardening (extra-gated §2) — *dep: G0*
- [ ] R4.1 · H7 · `render_as_batch=True` in `env.py` (leave compare_type default) · verify: autogenerate on SQLite doesn't error
- [ ] R4.2 · H2/Q5 · delete dead `HaEntity` model (+ any dangling import) · verify: import graph clean, no `ha_entities` reference
- [ ] R4.3 · H1+M11+M12+M13+M15+M0/Q10 · one batch-mode reconciliation migration: add 6 missing FKs (orphan-clean **first**), FK indexes, missing UNIQUE constraints, nullability/type drift, normalize enum defaults to names · verify: G-R4a/b/c/d gates
- [ ] R4.4 · H5 · rewrite broken `add_zones` downgrade (batch-drop 2 cols + zones only; stop touching `staff_pto.updated_at`) · verify: G-R4a round-trip
- [ ] R4.5 · H6 · re-run daily-recurrence data migration with correct enum casing (`WEEKLY`) · verify: seeded weekly rows actually update
- [ ] R4.6 · H17 · add `alerts.property_id` column (in this migration) + filter health-score alert deduction by property · verify: `tests/services/test_health_score.py::test_alert_scoped_to_property`
- [ ] R4.7 · M14 · `vendor_properties` association table (mirror `staff_properties`); migrate `vendor.property_ids` JSON; **no personal slugs in migrations** · verify: `tests/services/test_vendor_properties.py`

### [ ] G-R5 — Money type (int-cents TypeDecorator) — *dep: G-R4*
- [ ] R5.1 · M1/Q2 · `type/money.py` `Money` TypeDecorator (impl Integer, dollars↔cents); apply to all money columns · verify: `tests/models/test_money_type.py` (round-trip exact; no float drift)
- [ ] R5.2 · M1 · migration `CAST(ROUND(amount*100) AS INTEGER)` all money columns · verify: G-R4c data-preservation
- [ ] R5.3 · M2+M3+M4+M6 · finance math: `is not None` not `or` (0.0 cost); prorate quarterly/annual budgets; forecast Feb-29 + real-month divisor; budget audit old-value-first · verify: `tests/services/test_financial_report.py`, `test_budget.py`

### [ ] G-R1 — Ban silent swallows + smoke net — *dep: G0 (can parallel G-R4 conceptually; sequence after for clean suite)*
- [ ] R1.1 · R1 · replace every business-logic `except Exception: pass` with `logger.exception` across the census list (spec R1); **log-and-continue in gateway batch loops, never raise** · verify: each converted site has a test asserting the error now surfaces/logs
- [ ] R1.2 · R1 · smoke test: invoke every AI tool executor + every report section once vs seeded DB · verify: `tests/ai/test_smoke_all_tools.py` (this becomes §1.5 permanent net)
- [ ] R1.3 · L1 · rotating file-log handler in root CLI callback, level via `MIHOMES_LOG_LEVEL` · verify: `tests/cli/test_logging.py`

### [ ] G-Svc — Silent-corruption sweep (finance/health/recurrence/vendor/WO/AI logic) — *dep: G-R1, G-R5*
- [ ] S.1 · H15 · vendor_spending: exclude `source="work_order"` tx (or drop WO leg); filter by `completed_at` · verify: `tests/services/test_financial_report.py::test_no_double_count`
- [ ] S.2 · H16 · health-score budget overrun scoped to budget period window · verify: `test_health_score.py::test_budget_period_window`
- [ ] S.3 · H18 · `RecurrenceFrequency.DAILY` case in `calculate_next_due` · verify: `test_recurrence.py::test_daily`
- [ ] S.4 · H19 · `generate_transactions` while-loop catch-up; end_date per-occurrence · verify: `test_recurring.py::test_backfill`
- [ ] S.5 · H20 · calendar pull: skip unmatched events (**both** branches); dedup gcal ids; no turnover re-spawn on unchanged occupancy · verify: `test_calendar_sync.py`
- [ ] S.6 · H21/Q9 · vendor soft-delete (`active=False`); filter active in default lists · verify: `test_vendor.py::test_soft_delete_with_contract`
- [ ] S.7 · H22 · WO `complete()` validate cost (`is None`) **before** mutating; web route surfaces the error (not swallow) · verify: `test_work_order.py::test_complete_requires_cost`, `tests/web/test_work_orders.py::test_complete_error_surfaced`
- [ ] S.8 · H23 · converge issue↔WO link on `issue_id` (verify() + list both read it) · verify: `test_work_order.py::test_issue_link_converged`
- [ ] S.9 · H12 · per-request date in roles (not import-frozen) · verify: `test_roles.py::test_date_not_frozen`
- [ ] S.10 · H13 · provider image-capability flag; raise if active provider can't send images (**NIM=capable**); batch error-path catches raise · verify: `test_assessors.py::test_room_scan_requires_image_provider`
- [ ] S.11 · H14 · `agent_stream` worker opens own session + saves in worker + logs (no cross-thread session, no swallow) · verify: `tests/web/test_ai_stream.py`
- [ ] S.12 · M5+M10 · rating 1–5 validation shared helper; exact `func.lower(col)==` / escaped LIKE across the M10 census · verify: `test_vendor_rating.py`, `test_fuzzy_match.py`
- [ ] S.13 · M34+M35+M37 · situation-report max_tokens/stop_reason; `content[0]` guard + attachment handling in structured_output; NIM `media_type`; file_processor size cap · verify: `tests/ai/test_provider_content.py`

### [ ] G-R2 — Gateway dedup core — *dep: G-R1*
- [ ] R2.1 · R2 · extract `services/gateways/review_common.py` (schema, `_ai_response`, `_handle_approval_message`, dispatch, photo-attach, gateway-aware notifier, monitor-loop); parameterize by client · verify: `tests/gateways/test_review_common.py`
- [ ] R2.2 · H25 · WhatsApp approval derives phone from `sender` (`split("@")[0]`) · verify: `test_whatsapp_approval.py`
- [ ] R2.3 · H24 · route approver DMs into `_handle_approval_message` before propertySlug filter (**after H25**) · verify: `test_approval_routing.py`
- [ ] R2.4 · H35 · gateway-aware PTO notifier (Telegram approver chat-id config) — no WhatsApp hardcode · verify: `test_pto_notify.py::test_telegram_notified`
- [ ] R2.5 · H36+M38 · `Vendor.company_name` (not `.name`) in telegram `_resolve_vendor` + `cli/report.py:326` · verify: `test_vendor_resolve.py`, `tests/cli/test_report_upcoming.py`
- [ ] R2.6 · H26 · issue photo path via shared helper (correct parents / MEDIA_DIR) · verify: `test_photo_path.py`
- [ ] R2.7 · H27 · rollback guard in `_ai_response` (or subsumed by R2.1) · verify: covered by test_review_common
- [ ] R2.8 · H28 · clamp `getUpdates` limit ≤100, page by offset · verify: `test_telegram_client.py::test_limit_clamped`
- [ ] R2.9 · M21 · **poison-message guard**: mark IDs/attempt-count before processing (no ack-then-crash loss, no hot-loop) · verify: `test_offset_ack.py::test_crash_no_loss_no_hotloop`
- [ ] R2.10 · M22+M23 · one dedup store per gateway; insertion-ordered prune (`dict.fromkeys`, front); guard concurrent poll vs extractor · verify: `test_dedup.py`
- [ ] R2.11 · M24+L14 · unified review schema **and** dispatch; report skipped categories · verify: `test_review_dispatch.py`
- [ ] R2.12 · M25+M26+M27 · approval-failure feedback; group replies by jid; sender allowlist + generic group errors · verify: `test_gateway_safety.py`
- [ ] R2.13 · M28+M30+M31 · exact Windows PID field match; WhatsApp burst loop-until-drained; `telegram stop` also stops whatsapp+bridge; add `whatsapp stop` · verify: `test_gateway_lifecycle.py`
- [ ] R2.14 · M29 · bridge reconnect guard flag; rotate `messages.jsonl` on startup · verify: `tests/gateways/test_bridge.js` or documented manual check
- [ ] R2.15 · L12+L13+L15 · `_parse_event_date` via `fromisoformat` (%z); read `"note"` (not `"notes"`); lazy media download after property filter · verify: `test_gateway_misc.py`

### [ ] G-Web — Web hardening — *dep: G-R2 (R3 web half), G0.3*
- [ ] W.1 · M43/Q3 · **delete** `web/routes/reports.py` + `templates/reports.html` (dead) · verify: no import references; app still boots
- [ ] W.2 · H29 · budget chart `| tojson` on a dict (now budget.html-only) · verify: `tests/web/test_chart_escape.py`
- [ ] W.3 · H31+R3(web) · app-level handlers for `EntityNotFoundError`→404 / `AmbiguousIdentifierError`→400; delete dead `if not x` checks · verify: `tests/web/test_error_handlers.py`
- [ ] W.4 · M40+R3 · `AmbiguousIdentifierError <: ValueError`; add `services/parsing.py` (`parse_date`/`parse_money`); **audit web `except ValueError` sites** for the cross-cutting reach · verify: `tests/test_parsing.py`, `test_ambiguous.py`
- [ ] W.5 · H30 · Origin/Sec-Fetch-Site + Host middleware (reject cross-site POST, non-localhost Host) · verify: `tests/web/test_csrf_host.py`
- [ ] W.6 · H32 · `strict=True` zip on vendor contacts + surface ValueError as form error · verify: `tests/web/test_vendor_contacts.py`
- [ ] W.7 · M16 · route form/query parsing through `parse_money`/`parse_date` incl. the two `budget.py` `Form(float)` POSTs · verify: `tests/web/test_form_validation.py`
- [ ] W.8 · M17 · add hidden `active` input to **both** `vendors.html:339` and `staff.html:209` · verify: `tests/web/test_active_toggle.py`
- [ ] W.9 · M18 · DOMPurify around `marked.parse`; data-attr + delegated listener in `docs_section.html` · verify: `tests/web/test_html_escape.py`
- [ ] W.10 · M19+M20 · unassigned view filters books by `space_id is None`; AI upload endpoints size/count/type caps · verify: `test_unassigned.py`, `test_ai_upload_caps.py`

### [ ] G-CLI — CLI parsing + tail (R3 CLI half, P2/P3) — *dep: W.4 (parsing module)*
- [ ] C.1 · M39+M40 · CLI wraps `parse_date` → `typer.BadParameter`; resolver calls moved inside try across budget/report/asset/task/property · verify: `tests/cli/test_bad_input.py`
- [ ] C.2 · M41 · `import-csv` exit 1 when all rows fail · verify: `test_csv_cmd.py`
- [ ] C.3 · M42 · dashboard: aggregate in service, fetch after session materialized, surface errors · verify: `test_dashboard.py`
- [ ] C.4 · L-tier · L2 belle-estate default→None/sole-property; L3 real_data idempotency+due dates; L4 `--format` Enum; L5 Rich `escape()`; L6 `--accept` all-props reject; L7 `hide_input`; L8 PTO state guard; L9 nullable vendor scores; L10 residue; L11 create-property full page+hx-target · verify: targeted tests per item; batch-commit acceptable for pure-hygiene L items with a shared `tests/test_hygiene.py`

### [ ] G-Final — Compound-stop verification
- [ ] F.1 · full-suite `pytest -q` green (§1.4)
- [ ] F.2 · R1 smoke test green (§1.5, condition D)
- [ ] F.3 · every spec finding reconciled: landed-with-test OR in `opportunities.md` (condition B) — walk the spec top to bottom
- [ ] F.4 · `alembic revision --autogenerate` empty (schema==models)
- [ ] F.5 · write end-of-run report (§5)

---

## 5. Three-artifact insight discipline

Every non-trivial insight during the run lands in exactly one of three places — nothing is lost, nothing clutters the wrong file:

1. **`tasks/lessons.md`** — a *correction to how I work* (a mistake pattern + the rule that prevents it). Dated section, follows the existing format. E.g. "migration ran with FK enforcement on because of the Engine-level PRAGMA listener — always check for connect-listeners before assuming Alembic connections are unconstrained."
2. **`tasks/opportunities.md`** — *deferred work*: optimizations (one line, NOT acted on), new bugs found outside the DAG (candidate tasks w/ proposed severity), and blocked/poisoned tasks. This is the curated input to the **next** loop, never acted on in this one.
3. **End-of-run report** (below) — *what this run did*.

### End-of-run report (write to `tasks/build-loop-report.md` at STOP or HALT)
- **Status:** COMPLETE | HALTED (reason) — and which stop conditions A/B/C/D held.
- **Per-group:** commit sha, tasks done/poisoned, tests added, suite delta (before→after count, green/red).
- **Poisoned tasks:** id, spec-ref, why, what would unblock (cross-ref `opportunities.md`).
- **Spec reconciliation table:** every P0/P1/P2/P3/R item → {landed+test | deferred+why}. Condition B is provable from this table.
- **New bugs found:** count + pointer to `opportunities.md`.
- **Lessons captured:** count + pointer to `lessons.md`.
- **Verification evidence:** final `pytest -q` summary line, smoke-test result, empty-autogenerate confirmation.

---

## 6. Non-negotiables (from CLAUDE.md, enforced by the loop)

- **Test-first, always.** No fix without a regression test that failed before it. For R1, the test proves the *swallow* (fails before, on the swallowed error).
- **Minimal impact.** Only touch what the task names. New-scope bugs → `opportunities.md`, not silent side-fixes. The FIX-BREAKS notes exist because over-reaching already bit us.
- **Never mark done unfulfilled.** `[x]` requires green test + green affected-area suite. Group `[x]` requires full green + commit.
- **A1 Baileys is accepted risk.** Never re-raise the unauthenticated-bridge auth issue as work. (M29's reconnect/log bugs are separate and *are* in scope.)
- **Resumable by construction.** Commits + checkboxes are the only state. Any restart reads §4 and continues; no memory of the prior process required.
