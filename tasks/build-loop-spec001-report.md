# SPEC-001 Build Loop — End-of-Run Report

> **Generated:** 2026-08-10 · **Branch:** `worktree-spec-build-harness` → `origin/spec-build`
> **Baseline:** `be8d398` (`origin/main`) · **Spec:** `docs/specs/SPEC-001-phase0-landing-waitlist.md`
> **Invocation:** `/loop tasks/build-loop-spec001.md` (autonomous, no intermediate review stops)
> **This was the pilot run** — the first execution of the harness authored for SPEC-001…008.

---

## Status: ✅ COMPLETE

All five compound-stop conditions hold (conventions §0):

| Cond | Requirement | Result |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | ✅ G1–G9 + G-Final all `[x]`; **zero poisoned tasks** |
| **B** | every §6 step tasked **and** every §8 criterion gated | ✅ F.3a: 9/9 steps, 26 tasks, none unmapped · F.3b: 18/18 criteria, none ungated |
| **C** | full suite green | ✅ **1233 passed** · 1 failed (known, out of scope) · 1 skipped |
| **D** | smoke green | ➖ **N/A, declared not dropped.** Phase 0 ships a separate app (D1); no smoke file covers it. A11 is the structural equivalent and is green. |
| **E** | every §8 criterion green **by its own named test** | ✅ 18/18, run by node id, **20 passed / 0 skipped** |

**Supplementary gates:** landing DB holds exactly `{waitlist, alembic_version}`; main-tree
autogenerate reports *"No new upgrade operations detected"* with no `waitlist` op;
`alembic/versions/` unchanged vs `origin/main` (asserted by a test, not by hand); A18's guard
verified by injecting the wrong value and watching it fail.

---

## Per-group summary

| Group | Commit | Tasks | Tests | Suite after |
|---|---|---|---|---|
| G1 — `mihomes.ids` (UUIDv7) | `08ef75b` | 2/2 | 8 | 1086 |
| G2 — Waitlist model + landing migration tree | `9561c95` | 4/4 | 36 | 1122 |
| G3 — email package | `408e1cb` | 2/2 | 23 | 1145 |
| G4 — waitlist service | `9f557fd`, `fbc65e3` | 4/4 | 29 | 1174 |
| G5 — landing app skeleton | `938b62b` | 3/3 | 14 | 1188 |
| G6 — landing page (nine GTM sections) | `aa6f2ca` | 3/3 | 12 | 1200 |
| G7 — `POST /waitlist` + confirm | `5fa2709` | 3/3 | 10 | 1210 |
| G8 — Google OAuth stub | `8f95ad2` | 2/2 | 12 | 1222 |
| G9 — deploy artifacts | *(this commit)* | 3/3 | 11 | 1233 |
| G-Final — compound-stop verification | *(this commit)* | 5/5 | — | — |

**Suite delta:** baseline `be8d398` measured **1078 passed / 1 failed / 1 skipped** on Windows →
**1233 passed / 1 failed / 1 skipped**. **155 tests added.** 82 test files → 90.

---

## Poisoned tasks

**None.** No task hit the 3-attempt ceiling. The run-level circuit breaker (>5 poisons, a
poisoned prerequisite, or two consecutive group-gate failures) never tripped.

One task came within one attempt: `position()` in G4 took all three permitted attempts before
the root cause was found. See *Bugs found* below.

---

## Criteria reconciliation (conditions B and E — provable)

| # | Criterion | Test | State |
|---|---|---|---|
| A1 | UUIDv7 unique, time-ordered, version 7 | `test_ids.py::test_uuid7_properties` | ✅ |
| A2 | `new_id` works on the 3.11 floor | `test_ids.py::test_fallback_generates_valid_v7` | ✅ |
| A3 | Migration applies and reverses on Postgres | `test_migration_waitlist.py::test_upgrade_downgrade` | ✅ |
| A4 | Duplicate email updates, never a second row | `test_waitlist_service.py::test_signup_is_idempotent` | ✅ |
| A5 | Raw confirm token never persisted | `test_waitlist_service.py::test_token_stored_hashed_only` | ✅ |
| A6 | Confirm sets `confirmed_at`; second is a no-op | `test_waitlist_service.py::test_confirm_idempotent` | ✅ |
| A7 | Expired or unknown token does not confirm | `test_waitlist_service.py::test_confirm_rejects_bad_token` | ✅ |
| A8 | Email renders both HTML and text | `test_email_render.py::test_waitlist_confirmation_has_both_parts` | ✅ |
| A9 | Factory raises on an unknown provider | `test_email_provider.py::test_unknown_provider_raises` | ✅ |
| A10 | Send failure does not roll back the signup | `test_waitlist_routes.py::test_signup_survives_email_failure` | ✅ |
| A11 | **The single-user app is not reachable** | `test_landing_app.py::test_existing_routes_are_404` | ✅ |
| A12 | Identical response for new and existing emails | `test_waitlist_routes.py::test_no_email_enumeration` | ✅ |
| A13 | Rate limiting returns 429 past the threshold | `test_ratelimit.py::test_burst_is_limited` | ✅ |
| A14 | Valid ID token → waitlist row, no session | `test_oauth_stub.py::test_callback_creates_waitlist_row_only` | ✅ |
| A15 | Bad ID token signature rejected | `test_oauth_stub.py::test_callback_rejects_forged_token` | ✅ |
| A16 | No dollar amounts on the landing page | `test_landing_page.py::test_no_prices_rendered` | ✅ |
| A17 | `/healthz` returns 200 with DB reachable | `test_landing_app.py::test_healthz` | ✅ |
| A18 | DMARC omits strict alignment | `test_deploy_docs.py::test_dmarc_relaxed_alignment` | ✅ |

**Tally: 18/18 green by their own named tests, 0 skipped, 0 deferred.**

---

## The one deviation from the spec

**G2 — the migration does not live in `alembic/versions/` as §3's manifest says.** It lives in a
new `alembic_landing/` tree. The manifest contradicts the spec's own decisions:

- **D1** — the landing app *"shares the stack and nothing else."*
- **D3** — its database holds the **`waitlist` table only.**

Following the manifest would have replayed 40 revisions and created all 37 single-user tables in
the landing database. It also does not work: measured against PostgreSQL 18.4, the chain dies at
`e5f6a7b8c9d0_add_daily_recurrence.py` with `invalid input value for enum recurrencefrequency:
"weekly"` — that revision's docstring states its own assumption, *"SQLite stores enums as VARCHAR,
so no ALTER needed."*

And patching it would be discarded work: **SPEC-002 Step 6 squashes all 40 into `0001_pg_baseline`
and archives them as "reference only, never run."**

Decided with the founder before G2 ran. Logged in `opportunities.md` as a spec defect worth
folding back into SPEC-001 §3 by its author.

**G9 also deviates in a smaller way:** the manifest says `Dockerfile`; the landing image is
`Dockerfile.landing`, because the existing `Dockerfile` builds the single-user app for the Home
Assistant compose stack and is still in use.

---

## Bugs found during the run

Ten, all found by the harness's own gates rather than by review. The five that would have shipped
silently:

1. **`position()` counted unconfirmed rows and the row itself** (G4, three attempts). Root cause:
   SQLite persists the `postgresql.UUID` column as **undashed** hex while the ORM binds a dashed
   `uuid.UUID`, so SQL ordering on it matches nothing. Compounded by `created_at` coming from a
   server default, so tight-loop rows share a timestamp. Queue position returned `[4,4,4,4]` for
   four rows — **a wrong answer, not a crash**, which a green SQLite test would have shipped.
2. **The A1 time-ordering assertion passed by luck** (G1). 1000 UUIDv7 calls land inside 1 ms
   (measured: 2 distinct timestamps, 998 adjacent pairs sharing one), and RFC 9562 orders v7 only
   by its millisecond prefix. A coin-flip assertion that would have flaked in CI indefinitely.
3. **Off-by-one in the OAuth expiry check** (G8). `exp + SKEW < now` leaves a one-second hole
   exactly at the boundary — the only place a test lands.
4. **The `_UNMANAGED_TABLES` fix did not propagate** (G2). Two test modules keep their own local
   copies of the exclusion set, so adding `waitlist` to `env.py` broke both autogenerate oracles
   three modules away. Caught by the outer loop (§1.2).
5. **The landing Dockerfile was first written over the existing one** (G9), which
   `docker-compose.yml` still builds. Restored from HEAD; two tests now guard it.

Also: a Jinja block-rendering bug, an `lru_cache`/`monkeypatch` leak that poisoned three later
tests, a `secure` cookie that made `TestClient` look broken, a ruff `B017` blind-exception
assertion, and a comment that tripped my own grep-based test.

**New bugs found outside the DAG: none.** Nothing in the existing product broke.

---

## Unmet launch gates (blocks-ship, not blocks-build)

All four of SPEC-001's open decisions remain open. Per conventions §3.3 none poisoned a task —
every one is scoped to launch configuration or content, exactly as §1.3 says: *"None of these
block writing code. Each blocks launching."*

| # | Question | State |
|---|---|---|
| **O1** | ToS + Privacy published | **Open.** Footer links to `/legal/terms` and `/legal/privacy` **404 by design** — no placeholder pages were stubbed. Legally load-bearing before collecting the first real email. |
| **O2** | Founding-member offer terms | **Open.** Page and email both promise "a founding-member offer" and say nothing more. |
| **O3** | Waitlist gate number | **Open.** `confirmed_count()` reports the figure; the threshold is the founder's. |
| **O4** | Show queue position publicly? | **Open.** Default applied: computed, not displayed. |

**P3 (infrastructure) is unmet and non-halting:** Fly app, DNS delegation and Resend domain
verification are human actions. G9 produced every artifact; nothing was deployed.

---

## Lessons captured

**14 lessons** in `tasks/lessons.md`, dated 2026-08-06 and 2026-08-10. The transferable ones:

- A corrective harness's stop condition does not survive contact with constructive work — a stub
  satisfies "suite green + smoke green".
- Collection is not a pass; measure a baseline by running it. Record a known-failing baseline as
  known-failing rather than rounding it clean.
- On Postgres, "the first error was at revision 28" does not mean 27 revisions passed —
  transactional DDL rolls the whole chain back.
- A verified signature is not a verified token: check `aud`, `iss` and `exp` separately.
- "Do not leak whether a record exists" constrains the *error* paths, not just the happy one.
- Commit before the side effect when the side effect is allowed to fail.
- A duplicated exclusion list is a fix that does not propagate — grep for the other copies.

---

## Verification evidence

```
py -m pytest -q                       →  1233 passed, 1 failed, 1 skipped
   the 1 failure: tests/integration/test_backup.py::test_stale_pid_file_does_not_block_restore
   OSError: [WinError 87] — os.kill(pid, 0), a POSIX idiom Windows rejects.
   Pre-existing on the untouched baseline, out of scope, logged in opportunities.md.

F.2  18 criteria by node id          →  20 passed, 0 skipped
F.3a 9 §6 steps                      →  26 tasks, 0 unmapped
F.3b 18 §8 criteria                  →  0 ungated
landing DB tables                    →  ['alembic_version', 'waitlist']   (D3)
main-tree autogenerate               →  "No new upgrade operations detected"
alembic/versions/ vs origin/main      →  unchanged
A18 negative control                 →  injecting GTM:273's strict value FAILS the gate
route table                          →  exactly the 7-entry §7-N1 allowlist
/properties /staff /budget /ai         →  404
```

---

## What the next run should know

1. **The harness worked.** Five conditions, three-attempt ceiling, per-group commits as the only
   resume state. The run was paused and resumed twice with no loss — checkboxes plus commits were
   sufficient both times.
2. **F.3a/F.3b earned their split.** F.3b caught A18 ungated and Step 9 untasked *after* the
   artifacts were built — exactly the omission a single reconciliation walk would have missed, and
   the reason condition B is separate from condition E.
3. **The outer loop earned its place.** G2's fix broke tests three modules away; nothing in the
   inner loop would have seen it.
4. **SPEC-002 is next and it is the load-bearing phase.** Its Step 6 squash is *required*, not an
   optimization — the audit in `opportunities.md` sizes it: 21 native enum types, 3 empty-DB
   blockers, and two silent gaps where a green `upgrade head` would be a false pass.
5. **Author SPEC-002's harness with these lessons folded in**, per the plan's pilot-then-learn
   decision. In particular restate conditions C and D for it: Step 15 replaces `conftest.py`'s
   engine, so "the existing suite green" cannot mean the pre-existing suite.
