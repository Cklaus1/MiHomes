# SPEC-002 Build Loop — End-of-Run Report

> **Generated:** 2026-08-18 · **Branch:** `worktree-spec-build-harness` → `origin/spec-build`
> **Baseline:** `714fa1a` (SPEC-001 complete) · **Spec:** `docs/specs/SPEC-002-phase1-multitenant-foundation.md`
> **Invocation:** `/loop tasks/build-loop-spec002.md`, driven group-by-group across multiple sessions
> **This is the load-bearing phase** — SPEC-003 through SPEC-008 all describe this spec's design.

---

## Status: ✅ COMPLETE

All five compound-stop conditions hold (conventions §0):

| Cond | Requirement | Result |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | ✅ G1–G17 + G-Final all `[x]`; **zero poisoned tasks**. Three groups (G2, G13, G17) had headers marked done with sub-item checkboxes never flipped — found and fixed during G-Final's own walk, not left as a false signal. |
| **B** | every §6 step tasked **and** every §8 criterion gated | ✅ F.3a: 17/17 steps, one task each · F.3b: 23/23 criteria, none ungated (§2's map, "23/23 bound") |
| **C** | full suite green | ✅ **1562 passed**, 0 failed, 0 errors, 3 skipped (declared), 2 xfailed (declared, `strict=True`) — condition C's post-G15 baseline, confirmed stable across two independent runs |
| **D** | smoke green | ➖ No dedicated smoke file for this spec; F.1's full-suite gate is the structural equivalent, per the pilot's precedent |
| **E** | every §8 criterion green **by its own named test** | ✅ 23/23, run explicitly by node id — 25 passed (A20 parameterises to 3) |

**Supplementary gates:** the two association tables (`staff_properties`, `vendor_properties`) are
in the registry and A21-covered; the landing DB (SPEC-001) holds exactly
`{alembic_version_landing, waitlist}`, confirmed both by test and by a direct query; A21's raw-SQL
arm is verified on the non-superuser `app` role, not the superuser connection that would have
reported false green; every acceptance-criterion test was mutation-tested at least once during its
own group (G12, G17 in particular — see *Bugs found*).

---

## Per-group summary

| Group | What | Tests | Suite after (approx.) |
|---|---|---|---|
| G1 | Identity models — `Account`, `User`, `Membership`, `Invite`, `Session` (A2) | 16 | — |
| G2 | `TenantOwned` on 40 tables + explicit tenancy registry (A1) | — | — |
| G3 | Composite indexes lead with `account_id` — 43 non-leading → 17 | 4 | — |
| G4 | Child-table drift guard by trigger — 52 links | 4 | — |
| G5 | Per-account uniqueness — 15 slug tables + `tag.name` | 4 | — |
| G6 | `0001_pg_baseline` (43 tables, 52 triggers, 22 enums) + UUIDv7 PK conversion (G6.1/G6.1b) | 6 | — |
| G7 | `0002_rls` on 40 tables, `FORCE` | 11 | — |
| G8 | Scoped session — two §4.4 defects found and fixed | 11 | — |
| G9 | Connection hygiene — transaction-local GUC (N3) | 7 | — |
| G10 | Raw-SQL audit — zero `text(f"…")` in `src/` | — | — |
| G11 | `StorageProvider` — closed a live cross-tenant hole (the old `/uploads` static mount) | 33 | — |
| G12 | Auth — Google OIDC, sessions, CSRF, 8 controls mutation-verified | 23 | 1535 |
| G13 | CLI re-point — `--account`, `skip_tenant` census | +4 (G-Final) | 1462 (at G13) |
| G14 | `backup.py` + `doctor` — media-only, zero filesystem assumptions | 14 | 1546 |
| G15 | Test-suite migration to Postgres, `cli_database`, docker-compose Postgres (G15.4) | — | 1233→1235 (SQLite)→ Postgres baseline |
| G16 | Importer — 1,869 real rows imported, 0 dangling FKs | 11 | — |
| G17 | Isolation test (A21) — 11 tests, all 40 tables, mutation-verified | 11 | — |
| G-Final | Compound-stop verification — found and fixed 4 real gaps, not just confirmed | +9 (5 fixed files + `test_cli_account_flag.py`) | 1546→**1562** |

*Exact per-group suite-after counts were not captured for every group in real time (unlike the
SPEC-001 pilot); the ones recorded above are as stated in the group's own commit or harness entry.
The number that matters — the final one — is verified twice, directly, in this report.*

**Suite delta:** pre-SPEC-002 baseline **1233 passed / 1 failed / 1 skipped** (SQLite, per SPEC-001's
report) → **1562 passed / 0 failed / 0 errors / 3 skipped / 2 xfailed** (Postgres, tenant-aware).
**~330 tests added or migrated.**

---

## Poisoned tasks

**None.** No task hit the 3-attempt ceiling; the circuit breaker never tripped. Several tasks came
close to needing a second design pass (G8's two §4.4 defects, G17's toothless-arm findings) but
each converged within the loop's own mutation-testing discipline.

---

## Criteria reconciliation (conditions B and E — provable)

All 23 run explicitly by node id in one invocation, immediately before this report was written:

| # | Criterion | Test | State |
|---|---|---|---|
| A1 | Every non-global model subclasses `TenantOwned` | `test_tenancy_registry.py::test_all_models_tenant_owned` | ✅ |
| A2 | A second active owner per account is rejected | `test_membership.py::test_one_owner_partial_index` | ✅ |
| A3 | Two accounts can each hold the same slug | `test_per_account_uniqueness.py::test_slug_still_unique_within_one_account` | ✅ |
| A4 | Two accounts can each hold a "Plumbing" tag | `test_per_account_uniqueness.py::test_two_accounts_can_reuse_a_slug_and_a_tag_name` | ✅ |
| A5 | Query with no `current_account` raises `LookupError` | `test_scoped_session.py::test_fails_closed_without_context` | ✅ |
| A6 | Bulk `update()`/`delete()` are scoped | `test_scoped_session.py::test_bulk_ops_scoped` | ✅ |
| A7 | Insert is auto-stamped with the current account | `test_scoped_session.py::test_insert_stamped` | ✅ |
| A8 | RLS with unset GUC returns **zero rows**, not an error | `test_rls.py::test_unset_guc_returns_empty` | ✅ |
| A9 | RLS `WITH CHECK` rejects an insert stamped with another account | `test_rls.py::test_with_check_rejects_foreign_account` | ✅ |
| A10 | The account-picker query works before account context | `test_rls.py::test_membership_self_policy` | ✅ |
| A11 | Pooled connection reuse never leaks tenant context | `test_connection_hygiene.py::test_no_guc_leak_across_transactions` | ✅ |
| A12 | Child `account_id` cannot diverge from its parent's | `test_drift_guard.py::test_polymorphic_tables_are_documented_as_uncovered`* | ✅ |
| A13 | No f-string SQL remains | `test_no_raw_sql_interpolation.py::test_the_guard_actually_detects_the_pattern` | ✅ |
| A14 | Storage keys are tenant-prefixed; round-trip works | `test_storage.py::test_key_prefix_and_roundtrip` | ✅ |
| A15 | Google sign-in creates a user + session; forged token rejected | `test_auth.py::test_signin_flow`, `::test_rejects_forged_token` | ✅ |
| A16 | Session cookie is httpOnly, Secure, SameSite=Lax | `test_auth.py::test_cookie_flags` | ✅ |
| A17 | Revoking a membership denies access on the next request | `test_auth.py::test_revocation_immediate` | ✅ |
| A18 | `mihomes doctor` on hosted reports no false error | `test_ops_commands.py::test_doctor_no_filesystem_assumptions` | ✅ |
| A19 | Importer round-trips row counts and FK integrity | `test_importer.py::test_roundtrip_counts_and_fks` | ✅ |
| A20 | A failed import leaves no partial account | `test_importer.py::test_failure_leaves_nothing` (×3 params) | ✅ |
| A21 | **Isolation: A can never read/write/delete B, any path** | `test_isolation.py::test_cross_tenant_denied_all_models` | ✅ |
| A22 | Isolation holds for raw-SQL sites *(retargeted — see Deviations)* | `test_isolation.py::test_ai_tools_raw_sql_scoped` | ✅ |
| A23 | The existing test suite still passes under tenancy | full suite green (F.1) | ✅ |

*A12's real name diverges from the spec's own row (`test_child_account_mismatch_rejected` also
exists and passes in the same file) — `test_polymorphic_tables_are_documented_as_uncovered` is the
one the harness's own G4.2 entry names, asserting the *documented, accepted* scope of the guard.

**Tally: 23/23 green by their own named tests.**

---

## Deviations from the spec

Five found during pre-flight (`opportunities.md`, before G1 ran), all decided or retargeted rather
than silently absorbed:

1. **A22 + Step 17's "three `ai/tools.py` call sites"** — that file has zero `text()` calls (the
   hardening pass already rewrote them). Retargeted at `services/archive.py`'s raw-SQL retention
   path, the actual remaining raw-SQL surface. Node id kept for traceability.
2. **The two association tables** (`staff_properties`, `vendor_properties`) are invisible to
   `TenantOwned.__subclasses__()` — a mixin cannot reach a Core `Table`. The registry (G2.4/G2.5)
   is explicit and hardcoded rather than derived, specifically so A1/A21 cover these two.
3. **Step 10's `grep -rn 'text(f"'` verify clause** matches two false positives
   (`.write_text(f"...")`, `save_document_text(f"...")`) — replaced with an AST-based check (G10.2).
4. **37 tables have integer PKs; only `waitlist` had UUID.** Resolved via UUIDv7 app-side PKs
   (D2), done as its own pass (G6.1/G6.1b) rather than folded silently into Step 6.
5. **Every count in the spec's own text understated the work** — 33 test files vs. 95 measured,
   28-of-33 using the `session` fixture vs. 43-of-95, 36 domain tables vs. 40, one `__table_args__`
   model vs. four. Recorded, not treated as scope: the counts changed the *sizing*, not the plan.

**Four more found mid-run** (`opportunities.md`, "Found while RUNNING SPEC-002"): the §4.4 scoped-
session snippet as specified makes sign-in circular (G8/G12's shared finding — resolved via a Core
select for the one bootstrap read); Step 9's pool `checkin` `RESET` is not implementable against a
connection mid-transaction (replaced with an unconditional per-transaction stamp); Step 14's RPO
check names a vendor API that D13 never actually pins to code; the backup archive's own durability
on a volumeless host is a real, separate gap from the media round-trip itself.

---

## Bugs found during the run

Beyond the deviations above — real defects, not scope questions:

1. **The §4.4 filter lambda could not run at all** (G8) — `InvalidRequestError: Can't invoke Python
   callable get() inside of lambda`. The account id has to be read *before* the lambda closes over
   it, not inside it.
2. **The same filter, unconditionally applied, broke every SPEC-001 landing test** (G8) — fixed by
   checking `state.all_mappers` and skipping statements that touch no `TenantOwned` entity.
3. **The pool `checkin` `RESET` as specified crashes** (G9) — `can't change 'autocommit' now:
   connection in transaction status INERROR`. Replaced with `after_begin` stamping both GUCs
   unconditionally (NULL when unbound) every transaction, superseding the unimplementable design.
4. **21 `session.get(Model, …)` call sites accepted a non-UUID id and crashed with a 500** instead
   of a 404 (`CannotCoerce: cannot cast type integer to uuid`) — one was already covered by a test
   (a real, live bug, not scheduled work); fixed with one `get_by_id()` helper applied to all 14
   sites whose id arrives as a function parameter.
5. **The pre-existing static uploads mount was a live, unauthenticated cross-tenant hole** (G11) —
   any request that could reach the app could fetch any tenant's document by URL. Closed by
   removing the mount entirely (a static mount has nowhere to put an authorisation check) in favor
   of a tenant-checked download route.
6. **A17 (auth) is unimplementable as literally specified** (G12) — session lookup must re-read
   `memberships` every request, but `Membership` is `TenantOwned`, and authentication runs *before*
   any account context exists. Resolved with a Core select (no mappers, filter correctly skips it),
   with RLS's `membership_self` policy as the real boundary underneath.
7. **`_live_service_pids()` only caught `ProcessLookupError`**, a POSIX-only exception — Windows
   raises a plain `OSError` (`WinError 87`) for a stale pid instead, so the check silently mis-
   classified a dead process as live. Latent since SPEC-001; found and fixed during G-Final while
   writing `test_ops_commands.py`'s live-service-guard tests.
8. **A doctor comment asserted `Task.property_id` had "no modeled FK"** to justify an orphan check
   — false; it carries a real `ForeignKey("properties.id")` under `0001_pg_baseline`, so the check
   is unreachable through any normal write path. Comment corrected rather than left wrong (G14).
9. **`property list`'s UI regressed under the PK migration** (G-Final) — a 36-character UUID `ID`
   column steals enough width from the fixed 80-column table layout that the `Slug` column
   truncates `"beach-house"` before its 9th character, breaking an existing assertion. One instance
   of a 31-table-wide pattern (`opportunities.md`); fixed here, logged everywhere else.
10. **11 tests (`test_dedup.py`, `test_offset_ack.py`, `test_report_upcoming.py`) were never
    actually migrated off SQLite by G15**, despite `conftest.py`'s own docstring claiming one of
    them as a `cli_database` consumer. `db.init_db()`'s SQLite refusal (G6.2) turned a latent gap
    into 11 hard errors. Migrated during G-Final.
11. **G13's own verify clause had zero test coverage** — no test anywhere exercised the explicit
    `--account <slug>` CLI flag, only the implicit "sole account" default every other test's
    single-account install takes. Found during F.3a/F.3b; fixed with a dedicated two-account test.

**Mutation testing earned its place repeatedly.** G12 found the session-fixation test could not
fail (wrong user, wrong connection) and a conditionally-`skip`ping cookie-flag test that would have
skipped forever. G17 found two of A21's four arms had no teeth: disabling the ORM filter entirely
stayed green because RLS *also* blocked the read on that connection (defence in depth verifying
neither layer alone), and disabling the GUC left every negative assertion green because "returns
zero rows" is trivially true of a completely broken filter — closed by adding
`test_each_account_can_read_its_own_rows` as a positive control, which immediately caught its own
test polluting a fixture (G17.3).

**New bugs found outside the DAG, logged not fixed:** `scripts/start-mihomes.sh` is checked out
with CRLF line endings on Windows (`core.autocrlf=true`) and is bind-mounted as-is into the
`mihomes` Docker service's `sh /start.sh` — a second, independent way that service is broken on a
Windows host, on top of S7 below. `.gitattributes` added (G15.4) to stop new scripts suffering the
same fate; the existing file's line endings were not force-renormalized, since fixing it is outside
G15.4's declared scope.

---

## Unmet launch gates (blocks-ship, not blocks-build)

Per conventions §3.3, carried forward as visible rather than silently satisfied. **None of these
are counted in the 0 failures** — that is the point of listing them separately.

| # | What | Owner | State |
|---|---|---|---|
| **S1** | **Archival does not work.** `run_archival()` raises `ArchivalUnavailableError`; the archive tables are not created by any migration and are not tenant-aware. | **unowned — needs a retention decision from the founder** | Open |
| **S5** | **Drift for the four polymorphic tables is app-only.** No `entity_type`→table mapping exists to build a trigger from; a raw-SQL insert or a cross-tenant-sourced `entity_id` is unguarded. | accepted, documented (the spec permits app-only enforcement if stated) | Open, accepted |
| **S7** | **Demo mode is architecturally incompatible with Postgres-only tenancy.** `_seed_demo_db()` writes tenant-owned rows with no account bound, and even fixing that hits `init_db()`'s SQLite refusal immediately after. | **needs a Postgres demo database or the feature's retirement — a product decision** | Open, converted to `xfail(strict=True)` so it stays visible |
| — | **D14's restore rehearsal is a human action.** G14 built the checks (media backup freshness, storage integrity); rehearsing an actual restore and writing down the real RTO has not been done. | founder, before the first non-founder tenant | Open |
| — | **A22's vendor-backup-freshness check has no API to call.** D13 leaves the managed-Postgres vendor as "an implementation detail," so nothing in code can name which status API to query for the *database* half of A22-adjacent gate. Our own media-backup freshness is checked instead. | needs a vendor decision + credentials | Open |
| — | **The backup archive itself is not durable on a volumeless host.** `mihomes backup`'s output lands on local disk; a Fly machine with no persistent volume loses it on redeploy. | needs bucket versioning or a second, deliberately-provisioned backup bucket | Open |
| — | **31 CLI tables show a full UUID in an `ID` column**, the same class of bug fixed once in `property list`. | next loop — mechanical, per-table | Open, logged in `opportunities.md` |

**Closed during this run, listed for completeness:** the two SQLite-refuses-to-run gates (S2 —
`init_db()` now raises `UnsupportedBackendError` on SQLite; S3 — `mihomes init` works via the
account bootstrap); the association-table RLS-only coverage question (S4 — the runtime role is now
verified at startup; RLS-only coverage on the two association tables is accepted, not a gap); the
local-SQLite-install-unreachable gate (S6 — closed by the importer, 1,869 rows moved, 0 dangling
FKs).

---

## Lessons captured

Recorded in `tasks/lessons.md` throughout the run. The transferable ones:

- **Assert on structure, not source text — three separate occurrences of the same mistake** (a
  `waitlist` guard, an archive-table guard, a `skip_tenant` guard) before the rule generalized:
  parse the AST, don't grep prose.
- **A `with A(), B():` closes B before A** — the manager whose exit does real work must be
  innermost, or state it depends on is already torn down.
- **`except ProcessLookupError` is a POSIX assumption** wearing a specific-looking name; Windows
  needs the broader `OSError` for the same "process is gone" meaning.
- **Defence in depth means a test exercising both layers verifies neither** — pin each layer on
  the connection where it is the *only* defence present.
- **A suite of only negative assertions is satisfied by a system that returns nothing at all** — a
  positive control (a query that must find rows) is what catches a completely broken filter.
- **When mutation testing shows an arm has no teeth, suspect the fixture before the assertion.**
- **A comment that explains why a check still matters is a claim about the schema** — verify it
  against the model, the same way a claim about behavior gets verified against the code.
- **For any config path a test needs to redirect, pass the destination in as an argument** —
  `monkeypatch.setenv` against a value already bound at import time is a silent no-op that looks
  like isolation.
- **A checkbox is not evidence of the opposite of its state** — three groups (G2, G13, G17) had
  real, working, tested code behind an unflipped `[ ]`. The reconciliation walk exists to catch
  exactly this, on both sides.

---

## Verification evidence

```
py -m pytest -q  (run twice, independently)   →  1562 passed, 3 skipped, 2 xfailed
                                                   0 failed, 0 errors — both runs identical

F.2  23 criteria by node id                   →  25 passed (A20 parameterised ×3), 0 skipped
F.3a 17 §6 steps                              →  17 tasked, 0 unmapped
F.3b 23 §8 criteria                           →  23 gated, 0 ungated
F.4  skip_tenant census                       →  1 definition site, 1 check site, 0 application
                                                   use sites; zero exposure to audit
F.5  landing DB tables (direct query)         →  ['alembic_version_landing', 'waitlist']
F.5  landing app route allowlist              →  test_existing_routes_are_404 — ✅
A21  isolation, non-superuser role            →  test_app_role_is_not_a_superuser — ✅
     (A21's raw-SQL arm has zero enforcement on a superuser connection — confirmed
      this suite never runs that arm there)
```

---

## What the next run should know

1. **The bookkeeping gap is real and worth a habit change.** Three groups in this run had their
   header checked and their sub-items not, or vice versa (G13's header was `[ ]` while its commit
   message said "done"). None of the underlying work was actually missing — but the checklist
   cannot be trusted at a glance without the reconciliation walk, which is exactly what F.3a/F.3b
   exist for. Flip both the header and every sub-item in the same edit that lands the group's
   commit, not as an afterthought.
2. **"Full suite green" and "the tests I remembered to check are green" are different claims.**
   Five of the eleven bugs in this report were sitting in plain sight, dismissed across many turns
   as "pre-existing" and "out of scope" without being opened. Two were genuine G15 migration gaps
   masquerading as platform flakiness; one was a real UI regression; one was zero test coverage on
   the actual tenant-selection mechanism. Every one of them was closed in under an hour once
   actually investigated. **Investigate red before triaging it, especially at the final gate** —
   triage is not a substitute for reading the traceback.
3. **SPEC-003 (Phase 2: onboarding, team, RBAC) is next**, and it depends directly on this phase's
   `memberships`, `can()` (not yet built — Phase 1 ships tenant scoping only, role checks are
   Phase 2 per the spec's own §9 table), and `require_permission` (referenced by two gateway PRDs
   as if it exists; it does not yet).
4. **SPEC-006 (gateway tenancy) inherits a decision this phase made, not a gap.** Telegram/WhatsApp
   background processes are correctly tenant-scoped today via the CLI's `_bind_account` (verified:
   `watchdog.py` spawns `python -c "from mihomes.cli import app; app()" telegram monitor`, which
   goes through the same root callback every other command does) — SPEC-006's job is *linking* a
   sender to an account, not fixing an unscoped process.
5. **Three product decisions block launch, not this phase's build:** S1 (archival retention), S7
   (demo mode's future), and D14 (the restore rehearsal, and the vendor whose SLA sets the real
   RPO/RTO numbers). None of the three needs code to resolve; all three need a founder call.
