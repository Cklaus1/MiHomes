# SPEC-005 Build Loop — Phase 4: Polish + Email Lifecycle + GA

> **Input spec:** `docs/specs/SPEC-005-phase4-polish-email-ga.md` (1077 lines, *Ready to build*,
> **2 open decisions** — O1 drip content, O2 deletion grace — both blocks-ship; **3 inbound gates**
> from earlier specs block *launch*, not the build)
> **Conventions:** `tasks/build-loop-conventions.md` — stop condition, poison ceiling, circuit
> breaker, artifact routing are defined there and inherited here **unchanged**.
> **Branch:** `worktree-spec-build-harness`. **Target ref:** HEAD `661f6f5` (SPEC-004 complete).
> **Invocation:** `/loop tasks/build-loop-spec005.md`

**Every previous phase defended a boundary — between customers (Phase 1), inside one (Phase 2),
between what was paid for and what was not (Phase 3). Phase 4 is different**: it is where the
product stops being something the founder operates and becomes something strangers depend on. The
spec's own framing of the stake, and it sets three failure modes that are silent in a way the
earlier phases' were not:

> **A scheduler that never fires** leaves the dunning ladder, the trial sweep and the drips all
> dead while every test passes. **An export that is not tenant-scoped** hands one customer the
> whole database and looks like a working feature. **A deletion that misses a table** leaves
> personal data behind and is a regulatory finding, not a bug report.

**Exit criteria:** the six-bullet **GA definition of done** (`SAAS_PRD:189-196`) — the only spec in
the set whose exit criteria were written before the spec was. **A33** is the gate that proves them
present and none falsely green.

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, all four DB env vars set | ✅ PostgreSQL 18.x, `mihomes_test` + `mihomes_phase0` |
| P2 | `MIHOMES_SECRET_KEY` set | ✅ required since SPEC-003 U1 |
| P3 | SPEC-001 `services/email/` package + `EmailService` | ✅ `provider.py`, `service.py`, `render.py`, `console_provider.py`, `resend_provider.py` |
| P4 | SPEC-004: `EmailService`'s four billing methods, `mihomes jobs`, `AIUsageRollup`, limits module | ✅ all present at HEAD |
| P5 | A Stripe account | ❌ **still absent** — inherited from SPEC-004 §0.8 U2. Does not halt: only the dunning ladder's *live* behaviour is unprovable, and `FakeBillingProvider` covers the rest |
| P6 | A verified sending domain + DNS records | ❌ **not present.** D17 makes this a *documentation* test rather than a live DNS query precisely so the build does not depend on it |

**Environment — pass inline, the worktree guard rejects `export` chains:**

```
DATABASE_URL               postgresql+psycopg://postgres@localhost:5432/mihomes_test
MIGRATION_DATABASE_URL     postgresql+psycopg://postgres@localhost:5432/mihomes_test
TEST_DATABASE_URL          postgresql+psycopg://postgres@localhost:5432/mihomes_test
LANDING_TEST_DATABASE_URL  postgresql+psycopg://postgres@localhost:5432/mihomes_phase0
MIHOMES_SECRET_KEY         <Fernet key — `mihomes config generate-key`>
```

Invoke pytest as `py -m pytest`, never `python`. Use `--color=no` when parsing output.

---

## 0.3 Stop condition

Per conventions §0, all five. Conventions §0.1: *"SPEC-003 onward — C is suite green **including
this spec's new tests**."*

| | Condition | For SPEC-005 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 below |
| **B** | every §6 step tasked **and** every §8 criterion gated | F.3a + F.3b |
| **C** | full suite green **including this spec's new tests** | baseline below → final |
| **D** | smoke green | `tests/integration/test_smoke_all_tools.py` |
| **E** | every §8 criterion green by its own named test | **all 36**, F.2 |

**Measured baseline — HEAD `661f6f5`, correct env, before any SPEC-005 code:**

```
py -m pytest -q   →   2184 passed, 3 skipped, 2 xfailed, 0 failed   (328s)
```

The 3 skips and 2 xfails are inherited and known-benign. Conventions §0 makes **a new skip red**.

### 0.4 Condition E has three holes this spec cannot close by itself

Conventions §0: *"a stub can satisfy A+B+C+D."* Three of SPEC-005's criteria define their own scope
and would pass vacuously on a faithful implementation. Each gets a **derived** gate — the
construction used four times across SPEC-003/004 (`UNFILTERED_CLASSES`, `G-census`, `G-dispatch`,
`ALLOWLIST_MECHANISMS`): declare the decision as data, then derive the check from the code.

| Gate | Check | Closes |
|---|---|---|
| **G-purge** | Every `TenantOwned` table lands in **exactly one** of `DELETE` / `PRESERVE` / `ANONYMIZE`, each with a reason. A new model fails the suite until classified. | A28's *"enumerates from `Base.metadata`"* — and D18's trap, below |
| **G-export** | Every `TenantOwned` table appears in the export bundle, enumerated from metadata at test time. | A27 — a hand-listed export is F4's bug rebuilt |
| **G-jobs** | Every workload registered on `mihomes jobs` is exercised by the idempotence test, enumerated from the Typer app. | A15/A17 — a sixth workload added later must not skip the gate |

**D18's trap, stated so it is not rediscovered:** `ANONYMIZE` is **not a third exclusion**. Both
existing exclusions are *skips*, and **a skipped row keeps its `account_id`** — which retains
personal data after an erasure request. Anonymize is an `UPDATE`. The set is **empty but declared**
(`DEFERRED (SPEC-008)`: no table qualifies until Vendor Discovery's `VendorReview` exists), and the
derived test asserts the three-way partition is total regardless.

---

## 0.6 PRE-FLIGHT RE-VERIFICATION (conventions §3.1) — measured at HEAD `661f6f5`, 2026-08-25

**SPEC-005 was written 2026-08-04 and its §0.1 is now false — for the fourth spec running.** It
opens: *"All four preceding phases are unbuilt … `services/email/` **does not exist** … no
`Account`, `User`, `Membership`, or auth layer. No `can()`. No `/healthz`."* All four have since
landed. Conventions §3.1 forbids proceeding on an unverified premise in **either** direction.

### Claims that hold — verified, not assumed

| Claim | Source | Measured |
|---|---|---|
| `csv_io.export_csv` covers **5 of 28** model modules and calls `session.query(model).all()` with **no account filter** | F4 | ✅ `csv_io.py:33` — confirmed unfiltered. D14/N4 stand |
| `backup.create_backup` tars the whole DB + media dir, no account parameter | F5 | ✅ operator-only, must never be routed to a customer export |
| `run_predictive_maintenance` has **zero callers** | F6 | ✅ 1 hit = the definition itself |
| No observability library anywhere | F7 | ✅ zero hits for sentry/structlog/otel/prometheus |
| `email_suppressions` carries **no `account_id`** | §4.1 | ✅ a clean `GLOBAL_TABLES` entry — **no carve-out needed**, unlike SPEC-004's ledger |
| Three Estate keys exist and are `False` on Free **and Pro** | D10 | ✅ `limits.py:58-60,73-75` — SPEC-004's table swap already made them real, so D10's gates have live teeth waiting |

### Claims corrected — build against the right-hand column

| # | Claim | Spec | **Measured at HEAD** |
|---|---|---|---|
| **C1** | "All four preceding phases are unbuilt" | §0.1 | **All four landed.** 2184 tests green. Every forward reference in §5 now describes *code* |
| **C2** | "No `/healthz`" and Step 15's *"`/healthz` confirmed live from SPEC-001"* | §0.1, Step 15 | **Half true, and the half that matters is false.** `/healthz` exists in `landing/routes.py:41` — the **landing app**. The product app (`web/app.py::create_app`, 154 routes) has **none**. Step 15 must add it rather than confirm it |
| **C3** | "**136** `except Exception` blocks in `src/`" | F7 | **154** — and the number is the least of it. **A32 says *"the request path"***, which is 16 in `web/`, not 154 tree-wide. Scoping A32 to the tree turns one criterion into a 154-site refactor wearing an acceptance criterion's name. **A32 is scoped to `web/` plus the services its routes reach**; the rest is `opportunities.md` |
| **C4** | "**No FastAPI `exception_handler` is registered anywhere**" | F7 | **False.** `web/app.py:138,142` register two — `EntityNotFoundError`→404 and `AmbiguousIdentifierError`→400, from the hardening run. Step 15 *extends* the pattern; it does not introduce it |
| **C5** | "the only `logging.basicConfig` is `ha/bridge.py`" | F7 | **`logging_config.py` exists** with `setup_logging()` and a rotating file handler, wired into the CLI root callback (hardening L1). Step 15 replaces its config with a real `dictConfig`; it does not start from nothing |
| **C6** | §8 has "36 rows with a max label of `A34`" | conventions §4.2 | ✅ **verified exactly** — and this is the one that would have broken the harness. See below |

### C7 — the F.3b walk must derive labels, never range-check

Conventions §4.2: *"`A`-labels are strings, not integers. SPEC-005 has `A14b` and `A29b` — 36 rows
with a max label of `A34`. Never range-check or count by label number."*

Measured: **36 criteria, `A14b` and `A29b` lettered, max numeric `A34`.** SPEC-004's F.3b used
`range(1, 32)`, which here would **miss both lettered labels and falsely flag nothing** — the
numeric set happens to be gapless, so a range check would report a clean pass while never having
looked at two criteria. That is the exact false-green shape §0.4 exists to close, arriving in the
verification script rather than the code.

**F.3b parses §8's table for `A\d+[a-z]?` and compares that set against the DAG.**

### C8 — SPEC-005's fail-closed inheritance is the largest yet

Six SPEC-003/004 gates reject new models, and this phase adds **five tables** (§4.4). Every one
must be answered, not routed around:

| Gate | What it demands |
|---|---|
| `test_matrix.py::test_every_model_is_classified` | five `ENTITY_CLASSES` entries |
| `tenancy/registry.py::check_registry` | four in `TENANT_TABLES`, `email_suppressions` in `GLOBAL_TABLES` |
| `UNFILTERED_CLASSES` | an honest reason for whatever class the new models take |
| `test_baseline_matches_metadata` | UUID PKs, `DateTime` naive/aware matched to the mixin |
| **three pinned counts** | `test_pg_baseline.py` 50→55 · `test_tenancy_registry.py` 45→49 · `test_isolation.py::EXPECTED_TENANT_TABLE_COUNT` 45→49 |

All three counts fired correctly during SPEC-004 and cost thirty seconds each. **Budget for them
with reasons, do not argue with them.**

### C9 — Step 9's unsubscribe is the second `PERMANENT_ALLOWLIST` entry

RFC 8058 one-click unsubscribe is a **POST with no session** — the same shape as SPEC-004's Stripe
webhook, and for the same reason: the caller is a mail client, not a user. It goes in
`PERMANENT_ALLOWLIST`, and `ALLOWLIST_MECHANISMS` (built at SPEC-004 G4) now **requires** it to
name what authenticates instead — a signed token. That gate was written one step before it got its
second real user; it will fire correctly.

The Host/Origin guard exemption is the same question SPEC-004 G4 answered: a mail client POSTs
from anywhere. **Check whether `WEBHOOK_PATH_PREFIX`'s exemption already covers it or a second
prefix is needed** — do not assume either.

---

## 0.7 O1 and O2 — both blocks-ship, neither blocks the build

Conventions §3.3, and the spec has already done the split:

- **O1** (drip content and cadence) — *"**Template content and the schedule rows only.** The
  mechanism — enrolment, scheduling, suppression, unsubscribe — is fully specified below and
  testable with fixture templates. Step 11 ships the machinery; the copy lands in config."*
- **O2** (deletion grace length, and whether deletion can be cancelled) — *"**One config value and
  the cancel route.** The state machine (D15) is identical either way."*

**Do not route either to the user mid-run.** Both are recorded in §0.8; A31 asserts they are
*tracked*, not resolved.

## 0.8 UNMET LAUNCH GATES — carried forward, not silently satisfied

This is the **last spec in the set**: there is no §10 after it to inherit these, which is why the
list is long and why A31 exists to keep it honest.

| # | What | Owner |
|---|---|---|
| **U1** | **SPEC-001 O1 — ToS + Privacy Policy unpublished.** The oldest unresolved item in the set; footer links 404 today. Blocks `SAAS_PRD:194` directly, and legally blocks Phase 0's first email capture | founder |
| **U2** | **SPEC-004 O1 — ~20 placeholder prices and limits.** Blocks `:195`, public signup at real prices | founder |
| **U3** | **SPEC-003 O1 — at-rest secret encryption.** ✅ **CLOSED** by SPEC-003 U1 (Fernet, `enc:v1:`). §1.6's third inbound gate is **stale in the spec**; recorded here so a reader does not chase it | resolved |
| **U4** | **This spec's O1** — drip content and cadence | founder |
| **U5** | **This spec's O2** — deletion grace length and cancellability | founder |
| **U6** | **No Stripe account** (SPEC-004 U2, unchanged) — the dunning ladder's live behaviour is unprovable here | founder |
| **U7** | **No verified sending domain.** D17 makes deliverability a documentation test on purpose, so the build proceeds — but *"DKIM/SPF/DMARC passing"* (`:191`) is a DNS task with lead time | founder |
| **U8** | **GA ships single-provider.** `FailoverEmailProvider` deferred again: failover to an *unverified* standby is not failover, and `PostmarkProvider`/`SESProvider` are named in `BILLING` §2.3 and **specified nowhere** (F2) | Phase 5+ |
| **U9** | **`audit_log` is scoped by application logic, not a foreign key** (F8) — polymorphic `entity_type`/`entity_id`, no composite FK possible. `audit_export` inherits the weaker guarantee | accepted |
| **U10** | **Fly's scheduled-machine mechanism is still unverified against their documentation** (SPEC-004 D15, restated in Step 5). Needs the network; cannot be answered from the repo. **The interface half — idempotent subcommands — is buildable and is what A17 proves** | founder |
| **U11** | **The 138 `except Exception` blocks outside the request path** (154 total − 16 in `web/`). A32 is scoped to the request path by C3; the rest is a real cleanup with no acceptance criterion, logged in `opportunities.md` | deferred |
| **U12** | Inherited from SPEC-004 §10, unchanged: revenue correctness, the Stripe account's own configuration, cost attribution below the account, inference cost vs. price | as recorded |

---

## 1. Task DAG

Conventions §1.3: **one step per group by default**; the group commit is the resume point.
Format is conventions §4: `checkbox + ID · spec-ref · criteria · imperative · verify:`.

**Ordering constraints the spec names as load-bearing:** **Step 5 before Steps 10, 11 and 13**
(the scheduler exists before three workloads need it); **Step 6 before 7–14** (the migration
before anything reads its tables); **Steps 1–4 before 10–11** (transport, suppression, delivery
log and outbox before any lifecycle mail uses them).

### [ ] G1 — Step 1: the Protocol widening — *dep: none*
- [ ] G1.1 · §6 Step 1 · A19 · `EmailProvider.send()` gains **exactly one** additive keyword, `headers: dict[str,str] | None = None` (D11); both implementations keep working · verify: `tests/unit/test_email_provider.py::test_headers_is_the_only_widening`

### [ ] G2 — Step 2: suppression — *dep: G1*
- [ ] G2.1 · §6 Step 2 · A22,A23 · checked at **`EmailService._send`** — one choke point, so no `send_*` can forget it (D13); **absolute for lifecycle mail, inapplicable to transactional** · verify: `tests/integration/test_suppression.py`

### [ ] G3 — Step 3: the delivery log — *dep: G2*
- [ ] G3.1 · §6 Step 3 · A24 · every send records an attempt; `SAAS_PRD:168`'s third observability surface (D7) · verify: `tests/integration/test_delivery_log.py`

### [ ] G4 — Step 4: the outbox — *dep: G3*
- [ ] G4.1 · §6 Step 4 · A25,A26 · **a real table with a worker, not an in-process retry loop** (D12) — an in-process retry dies with the request and cannot survive a deploy · verify: `tests/integration/test_outbox.py`

### [ ] G5 — Step 5: the scheduler — *dep: G4 — MUST precede G10, G11, G13*
- [ ] G5.1 · §6 Step 5 · A15,A17 · `drain-outbox`, `dunning`, `drips`, `weekly-digest` on SPEC-004's `mihomes jobs`; **G-jobs** enumerates workloads from the Typer app · verify: `tests/integration/test_jobs.py::test_every_workload_is_idempotent`
- [ ] G5.2 · **U10** · — · Fly's mechanism **cannot be confirmed from the repo** — split per conventions §3.3: the interface half ships, the infra confirmation is recorded as an unmet gate · verify: §0.8 U10

### [ ] G6 — Step 6: the migration — *dep: G5 — MUST precede G7–G14*
- [ ] G6.1 · §6 Step 6 · A30 · §4.4's five tables, four RLS policies, one carve-out; applies and reverts · verify: `tests/integration/test_pg_baseline.py::test_upgrade_then_downgrade_is_clean`
- [ ] G6.2 · §6 Step 6 · A21 · `email_suppressions` has **no** policy — a suppressed address must stay suppressed after the account that surfaced it is gone · verify: `tests/unit/test_email_tenancy.py::test_suppressions_not_rls`
- [ ] G6.3 · C8 · — · five `ENTITY_CLASSES` entries, registry entries, **three pinned counts** raised with reasons · verify: `tests/unit/test_matrix.py::test_every_model_is_classified`

### [ ] G7 — Step 7: data export — *dep: G6*
- [ ] G7.1 · §6 Step 7 · A27 · `build_export` from `Base.metadata` under the scoped session — **never** `csv_io.export_csv` (5/28 tables, unfiltered) or `backup.create_backup` (whole DB + media) · verify: `tests/integration/test_export.py::test_every_tenant_table_is_exported`
- [ ] G7.2 · §6 Step 7 · A27 · **G-export** — a second account's rows never appear · verify: same module

### [ ] G8 — Step 8: deletion — *dep: G7*
- [ ] G8.1 · §6 Step 8 · A28 · two-phase `requested` → (grace) → `purged`; the purge enumerates from `Base.metadata` (D15) · verify: `tests/integration/test_deletion.py::test_every_tenant_table_is_purged`
- [ ] G8.2 · §6 Step 8 · A28 · **G-purge** — the three-way partition is **total**, and `ANONYMIZE` is declared-but-empty (`DEFERRED (SPEC-008)`), never conflated with a skip · verify: same module
- [ ] G8.3 · §6 Step 8 · A29,A29b · owner-only (D8); export offered first (D6) · verify: `tests/integration/test_deletion.py`

### [ ] G9 — Step 9: unsubscribe — *dep: G2, G6*
- [ ] G9.1 · §6 Step 9 · A18 · RFC 8058 one-click `List-Unsubscribe-Post`; signed token, no session · verify: `tests/integration/test_unsubscribe.py`
- [ ] G9.2 · C9 · — · second `PERMANENT_ALLOWLIST` entry **plus** its `ALLOWLIST_MECHANISMS` reason; check whether the Host/Origin exemption already covers the path · verify: `tests/unit/test_route_declarations.py::test_every_allowlisted_module_names_its_mechanism`

### [ ] G10 — Step 10: the dunning ladder — *dep: G5, G6*
- [ ] G10.1 · §6 Step 10 · A16 · escalating `dunning_2`/`dunning_3`/`dunning_final`; Phase 3 sends one `payment_failed`, the ladder is this phase's (SPEC-004 B2) · verify: `tests/integration/test_dunning.py`

### [ ] G11 — Step 11: the drip machinery — *dep: G5, G9*
- [ ] G11.1 · §6 Step 11 · A14,A14b · enrolment, scheduling, suppression, unsubscribe — **mechanism only**; content is O1 and lands in config · verify: `tests/integration/test_drips.py`

### [ ] G12 — Step 12: two Estate gates — *dep: G6*
- [ ] G12.1 · §6 Step 12 · A11,A12 · `predictive_maintenance` and `audit_export` as `can()` call sites (D10/D16) · verify: `tests/integration/test_estate_gates.py`

### [ ] G13 — Step 13: the weekly digest job — *dep: G5, G12*
- [ ] G13.1 · §6 Step 13 · A13 · `weekly_ai_report` enforced as a **send, not a gate** (D16) — the key names no scheduled anything, so the job must exist first · verify: `tests/integration/test_weekly_digest.py`

### [ ] G14 — Step 14: `audit_export` end to end — *dep: G12*
- [ ] G14.1 · §6 Step 14 · A34 · the gate sits on the **read/export** path, never on `record_change` — gating that would break every write in the app (F6) · verify: `tests/integration/test_audit_export.py`

### [ ] G15 — Step 15: observability and error handling — *dep: none*
- [ ] G15.1 · §6 Step 15 · A31 · real `dictConfig` (JSON in prod), FastAPI handlers **extending** the two that exist (C4), `error.html`, and **`/healthz` added to the product app** (C2 — it is landing-only today) · verify: `tests/web/test_error_handling.py`
- [ ] G15.2 · §6 Step 15 · A32 · no bare swallow **in the request path** — scoped per C3 to `web/` + the services its routes reach, not the 154 tree-wide · verify: `tests/unit/test_no_silent_swallows.py`

### [ ] G16 — Step 16: the deliverability check — *dep: none*
- [ ] G16.1 · §6 Step 16 · A20 · **B1's edit** — delete `adkim=s; aspf=s` from `GTM:273`; D17's documentation test, not a live DNS query · verify: `tests/unit/test_deliverability.py`

### [ ] G17 — Step 17: the GA readiness surface — *dep: all* — **the exit criterion**
- [ ] G17.1 · §6 Step 17 · A33 · one surface enumerating the six `SAAS_PRD:189-196` gates with status, the three §1.6 inbound gates **explicitly unresolved** where they are · verify: `tests/integration/test_ga_readiness.py::test_no_false_green`

### [ ] G-Final — Compound-stop verification (conventions §4.1)
- [ ] F.1 · full-suite `pytest -q` green (condition C)
- [ ] F.2 · every §8 criterion green by its own named test (condition E) — **all 36, run by node id**
- [ ] F.3a · walk §6 top-to-bottom: every step has a task (condition B, steps) — **17 steps**
- [ ] F.3b · walk §8 top-to-bottom: every criterion has a gate (condition B, criteria) — **parse `A\d+[a-z]?`, never range-check (C7)**
- [ ] F.4 · smoke green (condition D)
- [ ] F.5 · write end-of-run report `tasks/build-loop-spec005-report.md` (§5)

---

## 2. Group-specific gates (conventions §2)

| Group | Gate | Failure class it targets |
|---|---|---|
| **G6** | migration round-trip + metadata-drift + the three pinned counts | reversibility, convergence, silent table addition |
| **G7** | **G-export derived from `Base.metadata`** | F4's bug rebuilt — a hand-listed export omits 82% and looks fine |
| **G8** | **G-purge, three-way partition total** | a missed table is a regulatory finding, not a bug report |
| **G5** | **G-jobs enumerated from the Typer app** | a sixth workload added later skipping the idempotence gate |
| **G15** | A32 scoped to the request path (C3) | one criterion becoming a 154-site refactor |

**Mutation-check every security-, money- and privacy-relevant arm**: break it, confirm red,
restore. SPEC-004 found four tests green for the wrong reason this way — A22 was vacuous three
separate ways — and none was visible by reading.

**A surviving mutation has three diagnoses**, and "add a test" is right for one: redundant
condition (delete), untested arm (add the test), inert difference (document with the measurement).

## 2.1 RUN STATE — where a resuming session picks up

**Not started.** Pre-flight complete (§0.6), baseline measured (2184 passed), harness written.
Resume at **G1.1**.

## 3. Circuit breaker (conventions §3)

Halt and write the report with status `HALTED` if: more than **5** tasks poison, **or** G6 poisons
(nothing from G7 onward can be built without its tables), **or** G5 poisons (three workloads depend
on it), **or** two consecutive groups fail their full-suite gate.
