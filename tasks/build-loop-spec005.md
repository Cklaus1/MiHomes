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

### 0.5 The criteria column is derived — because the first version of it was typed

The first draft of §1's DAG assigned its criteria and `verify:` columns **by hand**. Measured
against §8, it was wrong in three ways at once:

- **Ten criteria — A1 through A10 — had no gate at all.** Not missing *work*: every one belongs to
  a group that already existed (suppression, outbox, export, deletion). They were simply never
  written down, so condition B would have passed a DAG covering 26 of 36.
- **Seven groups claimed the wrong labels.** G1 claimed A19 (a Step 3 criterion), G10 claimed A16
  (Step 4's), G12 claimed A11 (Step 2's). Every mutation-check in those groups would have aimed at
  the wrong criterion.
- **Nineteen `verify:` paths named files the spec does not use.** `test_drips.py` for §9's
  `test_campaigns.py`; `test_deliverability.py` for `test_docs_dns.py`; `tests/integration/` for
  four files §9 places in `tests/unit/`.

**What actually failed is worth more than the fix.** C7 — *"parse `A\d+[a-z]?`, never
range-check"* — was correct, and the pre-flight ran `probe_labels.py` to establish it. But that
probe enumerated **only the spec's labels**: the left-hand side of a comparison whose right-hand
side was never built. A verification that could not fail, written by the same pre-flight whose
stated purpose was closing exactly that shape. §0.4 exists to catch it in the implementation; it
arrived one level up, in the checking.

So F.3b is now a committed script, `scripts/spec005_reconcile.py`, and it runs **after every group
commit** rather than once at G-Final. It joins three sources and exits non-zero on any disagreement:

| Source | Authoritative for |
|---|---|
| **§8** | what each label means, and **which test discharges it** |
| **§9** | which **directory** that test lives in — §8 gives bare basenames |
| **§1** | what this DAG claims |

Three judgment calls, made once and recorded so they are not re-litigated:

- **A21 is dual-cited** by §6 Steps 2 and 6. It gates at **G6.2**: the criterion is that the
  migration omits a policy, and the migration is Step 6's.
- **A9's file disagrees between §8 and §9.** §8 names `test_deletion.py::test_cancel`; §9 puts the
  "cancel window" under `test_privacy_routes.py`. **§8 wins** — it names the test, §9 only
  describes coverage — so A9 gates at G8.3 and A8 at G8.4.
- **G6.1 cannot use `test_pg_baseline.py`.** §9 states plainly that no existing test exercises
  Alembic and A30 needs `test_migration_phase4.py` with **its own engine**. The first draft pointed
  at the existing baseline test, which builds schema from `Base.metadata` and would have proved
  nothing about the migration.

**G1 keeps an empty criteria cell on purpose.** Step 1's *Verify* line cites A18, but A18's declared
test is Step 9's `test_unsubscribe.py` — a `headers` kwarg nothing populates proves nothing. G1 is
enabling-only, and its own test guards N1 instead.

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

**F.3b parses §8's table for `A\d+[a-z]?` and compares that set against the DAG** — as `scripts/spec005_reconcile.py`, run after every group commit. Stating the rule was not enough: see §0.5 for what happened when it was stated and only half-executed.

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

**Measured at G3: there is a seventh, and C8 missed it.** `test_u7_enforcement.py::test_the_no_linkage_group_is_exactly_the_models_with_neither_shape`
pins the set of models staff are denied outright, and a new `ACCOUNT_LEVEL` model with no
property linkage lands in it. It fired on `EmailDelivery` and was right to: `template` names
which billing event occurred, so `dunning_3` would tell a housekeeper the household's card has
failed three times.

It did **not** fire at G2, which is the part worth carrying forward — `EmailSuppression` is
`GLOBAL`, outside that partition entirely. So the gate a new model trips depends on its entity
class, and G2's clean run is not evidence the next model will have one. **Four tenant tables
remain** (`email_outbox`, `campaign_enrolments`, `account_deletion_requests`, and Step 4's), and
each should expect all four counts plus this.

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

**The criteria and verify columns are derived, not typed** — see §0.5. `scripts/spec005_reconcile.py`
joins §8 (which label means what, and which test discharges it) with §9 (which directory that test
lives in) and fails if this table disagrees with either. Run it after every group commit.

**Ordering constraints the spec names as load-bearing:** **Step 1 before everything email** (the
Protocol change is one line and six later steps depend on it); **Step 5 before Steps 10, 11 and 13**
(the scheduler exists before three workloads need it); **Step 6 before 7–14** (the migration before
anything reads its tables); **Step 7 before Step 8** (export exists before deletion offers it);
**Step 12 before Step 13** (the gate exists before the scheduled send is gated).

### [x] G1 — Step 1: the Protocol widening — *dep: none*
- [x] G1.1 · §6 Step 1 · — · `EmailProvider.send()` gains **exactly one** additive keyword, `headers: dict[str,str] | None = None` (D11); both implementations pass it through, `ConsoleProvider` prints it · verify: `tests/unit/test_email_provider.py::test_headers_is_the_only_widening`

> **G1 carries no §8 criterion, deliberately.** Step 1's *Verify* line cites A18, but A18's declared
> test is `test_unsubscribe.py` — Step 9's file, because a header kwarg that nothing populates
> proves nothing. G1 is enabling-only; **A18 lands at G9.1**. Its own test guards N1 (*"do not widen
> the Protocol beyond `headers`"*), which no §8 row covers.

### [x] G2 — Step 2: suppression, the `klass` choke point, and the HMAC token — *dep: G1*
- [x] G2.1 · §6 Step 2 · A1,A2 · suppression checked at **`EmailService._send`** — one choke point, so no `send_*` can forget it (D13); **absolute for lifecycle mail, inapplicable to transactional** · verify: `tests/unit/test_suppression.py::test_lifecycle_suppressed` + `::test_transactional_ignores_suppression`
- [x] G2.2 · §6 Step 2 · A3 · `_send`'s `klass` is **keyword-only and never defaulted** — a default is how lifecycle mail silently becomes unsuppressible · verify: `tests/unit/test_email_service.py::test_klass_required`
- [x] G2.3 · §6 Step 2 · A22,A11 · `suppress` twice is a no-op; the unsubscribe token is HMAC-signed and a forged one is rejected · verify: `tests/unit/test_suppression.py::test_idempotent` + `::test_token_hmac`

### [x] G3 — Step 3: the delivery log — *dep: G2*
- [x] G3.1 · §6 Step 3 · A19 · every send records **exactly one** attempt carrying the provider message id; `SAAS_PRD:168`'s third observability surface (D7) · verify: `tests/integration/test_delivery_log.py::test_one_row_per_send`

### [x] G4 — Step 4: the outbox — *dep: G3*
- [x] G4.1 · §6 Step 4 · A4,A5 · **a real table with a worker, not an in-process retry loop** (D12) — an in-process retry dies with the request and cannot survive a deploy; and a send failure never rolls back its caller's transaction · verify: `tests/unit/test_outbox.py::test_retry_preserves_message` + `::test_send_failure_does_not_rollback`
- [x] G4.2 · §6 Step 4 · A16 · the five-rung backoff ladder; the fifth failure sets `failed_at` and the row **stops being selected** · verify: `tests/unit/test_outbox.py::test_backoff_ladder`

### [x] G5 — Step 5: the scheduler — *dep: G4 — MUST precede G10, G11, G13*
- [x] G5.1 · §6 Step 5 · A15 · **G-jobs** enumerates workloads **from the tree** and asserts each is registered and reachable — a seventh added later must fail the suite · verify: `tests/unit/test_jobs_enumeration.py::test_all_workloads_scheduled`
- [x] G5.2 · §6 Step 5 · A17 · `drain-outbox`, `dunning`, `drips`, `weekly-digest` on SPEC-004's `mihomes jobs`; every subcommand a no-op on a second consecutive run · verify: `tests/integration/test_jobs.py::test_idempotent`
- [!] G5.3 · **U10** · — · Fly's mechanism **cannot be confirmed from the repo** — split per conventions §3.3: the interface half ships and A15/A17 prove it, the infra confirmation is recorded as an unmet gate · verify: §0.8 U10

### [x] G6 — Step 6: the migration — *dep: G5 — MUST precede G7–G14*
- [x] G6.1 · §6 Step 6 · A30 · §4.4's five tables, four RLS policies, one carve-out; **its own engine running real Alembic up and down** — §9 states no existing test exercises Alembic, so `test_pg_baseline.py` cannot discharge this · verify: `tests/integration/test_migration_phase4.py::test_up_down`
- [x] G6.2 · §6 Step 6 · A21 · `email_suppressions` has **no** policy — a suppressed address must stay suppressed after the account that surfaced it is gone · verify: `tests/unit/test_email_tenancy.py::test_suppression_not_rls`
- [x] G6.3 · C8 · — · five `ENTITY_CLASSES` entries, registry entries, **three pinned counts** raised with reasons · verify: `tests/unit/test_matrix.py::test_every_model_is_classified`

### [x] G7 — Step 7: data export — *dep: G6*
- [x] G7.1 · §6 Step 7 · A27 · **G-export** — `build_export` enumerates from `Base.metadata` under the scoped session; **never** `csv_io.export_csv` (5/28 tables, unfiltered) or `backup.create_backup` (whole DB + media) · verify: `tests/integration/test_export.py::test_covers_all_tenant_tables`
- [x] G7.2 · §6 Step 7 · A6,A26 · no row belonging to another account appears anywhere in the bundle; documents are presigned references, not inlined bytes · verify: `tests/integration/test_export.py::test_no_cross_tenant_rows` + `::test_tenant_isolation`

### [x] G8 — Step 8: deletion — *dep: G7*
- [x] G8.1 · §6 Step 8 · A28 · **G-purge** — the purge enumerates from `Base.metadata` and applies **exactly one** disposition per table; the three-way partition is **total**, and `ANONYMIZE` is declared-but-empty (`DEFERRED (SPEC-008)`), never conflated with a skip (D18) · verify: `tests/integration/test_deletion.py::test_purge_dispositions_all_tables`
- [x] G8.2 · §6 Step 8 · A7,A29,A29b · zero rows survive in every `DELETE` table; `account_deletion_requests` and `email_suppressions` survive **untouched**; no account-referencing column on a **global** table still points at the purged account · verify: `tests/integration/test_deletion.py::test_purge_complete` + `::test_deliberate_survivors` + `::test_no_dangling_global_refs`
- [x] G8.3 · §6 Step 8 · A9,A10 · the `requested → grace → purged` state machine; a cancel restores normal service; **storage objects are deleted before their rows** — the reverse orphans blobs no row names · verify: `tests/integration/test_deletion.py::test_cancel` + `::test_storage_before_rows`
- [x] G8.4 · §6 Step 8 · A8 · deletion is **owner-only** (D8); admin and staff denied; export offered first (D6) · verify: `tests/integration/test_privacy_routes.py::test_owner_only`

### [x] G9 — Step 9: unsubscribe — *dep: G2, G6*
- [x] G9.1 · §6 Step 9 · A18 · RFC 8058 one-click `List-Unsubscribe-Post`; **lifecycle carries both headers, transactional carries neither**; one click, no confirmation page · verify: `tests/integration/test_unsubscribe.py::test_headers_by_class`
- [x] G9.2 · C9 · — · second `PERMANENT_ALLOWLIST` entry **plus** its `ALLOWLIST_MECHANISMS` reason (a signed token); check whether the Host/Origin exemption already covers the path · verify: `tests/unit/test_route_declarations.py::TestAllowlistDiscipline::test_every_allowlisted_module_names_its_mechanism`

### [x] G10 — Step 10: the dunning ladder — *dep: G5, G6*
- [x] G10.1 · §6 Step 10 · A23,A24 · one `invoice.payment_failed` produces **one** immediate email and the rest on the `BILLING` §5 schedule; recovery mid-ladder stops the sequence · verify: `tests/integration/test_dunning.py::test_ladder_schedule` + `::test_recovery_stops_ladder`

### [x] G11 — Step 11: the drip machinery — *dep: G5, G9*
- [x] G11.1 · §6 Step 11 · A25 · enrolment, `due_sends`, sequence shortening — **mechanism only**, content is O1 and lands in config; each step sends once and never twice · verify: `tests/unit/test_campaigns.py::test_no_duplicate_steps`

### [x] G12 — Step 12: two Estate gates — *dep: G6*
- [x] G12.1 · §6 Step 12 · A12 · `predictive_maintenance` and `audit_export` as `can()` call sites; Free and Pro denied, Estate allowed, on **both** (D10/D16) · verify: `tests/unit/test_estate_gates.py::test_gate_matrix`
- [x] G12.2 · §6 Step 12 · A13 · **`record_change` still fires for every account on every plan** — the check that catches gating the wrong function (F6) · verify: `tests/unit/test_estate_gates.py::test_audit_write_ungated`

### [ ] G13 — Step 13: the weekly digest job — *dep: G5, G12*
- [ ] G13.1 · §6 Step 13 · A14,A14b · `weekly_ai_report` enforced as a **send, not a gate** (D16): Estate receives it weekly, Pro does not, and the on-request route at `web/routes/ai.py:311` works on **every** plan — Estate buys the schedule, not the feature · verify: `tests/integration/test_weekly_digest.py::test_scheduled_send_gated` + `::test_on_request_ungated`

### [ ] G14 — Step 14: `audit_export` end to end — *dep: G12*
- [ ] G14.1 · §6 Step 14 · A34 · route + CLI; every `Denied` names an `upgrade_target`. The gate sits on the **read/export** path, never on `record_change` — gating that would break every write in the app (F6) · verify: `tests/unit/test_estate_gates.py::test_denied_names_target`

### [ ] G15 — Step 15: observability and error handling — *dep: none*
- [ ] G15.1 · §6 Step 15 · A31 · real `dictConfig` (JSON in prod), FastAPI handlers **extending** the two that exist (C4), `error.html`, and **`/healthz` added to the product app** (C2 — it is landing-only today); one structured log record with a request id · verify: `tests/unit/test_errors.py::test_handler_and_log`
- [ ] G15.2 · §6 Step 15 · A32 · no bare swallow **in the request path** — scoped per C3 to `web/` + the services its routes reach, not the 154 tree-wide · verify: `tests/unit/test_errors.py::test_no_silent_swallow`

### [ ] G16 — Step 16: the deliverability check — *dep: none*
- [ ] G16.1 · §6 Step 16 · A20 · **B1's edit** — delete `adkim=s; aspf=s` from `GTM:273`; D17's documentation test, not a live DNS query · verify: `tests/unit/test_docs_dns.py::test_dmarc_relaxed`

### [ ] G17 — Step 17: the GA readiness surface — *dep: all* — **the exit criterion**
- [ ] G17.1 · §6 Step 17 · A33 · one surface enumerating the six `SAAS_PRD:189-196` gates with status, the three §1.6 inbound gates **explicitly unresolved** where they are; **none reports a false green** · verify: `tests/integration/test_ga_readiness.py::test_all_gates_tracked`

### [ ] G-Final — Compound-stop verification (conventions §4.1)
- [ ] F.1 · full-suite `pytest -q` green (condition C)
- [ ] F.2 · every §8 criterion green by its own named test (condition E) — **all 36, run by node id**
- [ ] F.3a · walk §6 top-to-bottom: every step has a task (condition B, steps) — **17 steps**
- [ ] F.3b · `py scripts/spec005_reconcile.py --collect` exits 0 (condition B, criteria) — **derived, never range-checked (C7)**; `--collect` also proves every declared node id resolves
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
| **every** | `py scripts/spec005_reconcile.py` after each group commit | the DAG drifting from §8 — which it already did once, §0.5 |

**Mutation-check every security-, money- and privacy-relevant arm**: break it, confirm red,
restore. SPEC-004 found four tests green for the wrong reason this way — A22 was vacuous three
separate ways — and none was visible by reading.

**A surviving mutation has three diagnoses**, and "add a test" is right for one: redundant
condition (delete), untested arm (add the test), inert difference (document with the measurement).

## 2.1 RUN STATE — where a resuming session picks up

**In progress — G1–G11 done, G12 next.** Pre-flight complete (§0.6), baseline measured
(2184 passed), harness written, and §1's DAG **rewritten from a derived join** after the
hand-typed version proved wrong in three ways (§0.5). `py scripts/spec005_reconcile.py` exits 0:
36/36 criteria gated, every gate pointing at the test §8 declares.

**G1 complete** (Protocol widening, mutation-checked five ways). **G2 complete** — suppression,
the `klass` choke point, the HMAC token, mutation-checked ten ways; suite **2213 passed**. A21 is
green early, discharged by G2's own migration (§2.2 BD1). **G3 complete** — `EmailDelivery`,
migration `0013`, the write placed adjacent to the successful `provider.send()` so it travels
into `drain` at G4 unchanged; mutation-checked six ways; suite **2224 passed**.

**G4 complete** — `EmailOutbox` + `0014`, `_send` enqueues, the ladder pinned attempt by
attempt, mutation-checked eleven ways; suite **2246 passed**. **G2's mutations re-run and A1's
vacuum closed** (§2.2 BD5).

**G5 complete** — four new workloads, `SCHEDULE` as the one source of truth, `mihomes cron
setup` derived from it, mutation-checked eight ways; suite **2265 passed**. G5.3 is `[!]`:
U10's infra half cannot be answered from the repo (§0.8), and the interface half is what A15/A17
prove.

**G6 complete** — `CampaignEnrolment` + `AccountDeletionRequest` in `0015`, and A30 running
**real Alembic up→down→up** for the first time in this repo; mutation-checked six ways; suite
**2277 passed**. All five §4.4 tables now exist.

**G7 complete** — `privacy/export.py`, the owner-only route, mutation-checked six ways; suite
**2292 passed**. A6 caught a **real cross-tenant leak in my own first implementation** (§2.2 BD11).

**G8 complete** — the state machine, the three-disposition purge, the deletion routes, and a
**derived** populated-account fixture (one row in all 49 tenant tables, from `Base.metadata`);
mutation-checked ten ways; suite **2317 passed**. `SAAS_PRD:193`'s GA gate — export *and*
deletion — is now met.

**G9 complete** — the RFC 8058 route, both headers on lifecycle mail and neither on
transactional, the second `PERMANENT_ALLOWLIST` entry with its mechanism, and its **own**
Host/Origin exemption; mutation-checked nine ways; suite **2327 passed**.

**G10 complete** — the four-rung ladder on the outbox, three new templates, and the webhook
seam SPEC-004 left open; mutation-checked eleven ways; suite **2349 passed**.

**G11 complete** — `campaigns.py`, enrolment on account creation, the `drips` job, three
placeholder templates; mutation-checked ten ways.

**G12 complete** — and it found four defects in code that already claimed Step 12 was done
(§2.2 BD18). The `predictive_maintenance` gate was a **no-op on every plan**; `pro` carried an
Estate key; `can()` and `_upgrade_target` defaulted to different tables, so a denial named a
plan that also denied; and `audit.export` swallowed its own denial. Mutation-checked three
ways; 61 passed across the affected area.

Resume at **G13.1** — the weekly digest job. Note the survey findings that change its shape:
`cli/jobs.py`'s `weekly-digest` is a **print-only stub** (`"Weekly digest: 0 account(s) sent."`,
no imports, no DB), `generate_estate_digest` (`services/ai/reports.py:208`) has **no** gate and
must keep none (N8), and its route is `POST /ai/estate-digest` (`web/routes/ai.py:388`) carrying
only the RBAC declaration `ai.use`. **`tests/conftest.py` sets `DEFAULT_FIXTURE_PLAN = "estate"`
and there are no per-plan account fixtures** — so A14's "a Pro account does not receive it" needs
an explicitly-planned account or it passes vacuously.

Also carried forward for the groups after it: **G16's B1 edit is already applied** — `GTM:273`
reads `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai` with no `adkim=s; aspf=s`, so G16 is the
D17 documentation *test* only. And **`SAAS_PRD:190-195` is five bullets, not six** — G17/A33
must enumerate what the doc actually contains.

Run `py scripts/spec005_reconcile.py --collect` after every group commit, not only at G-Final.

## 2.2 DEVIATIONS from the spec, with the measurement that forced each

> **Labelled `BDn` — build deviation — not `Dn`.** The spec has its own `D1`–`D18` (design
> decisions) and these were originally numbered in the same namespace, so "D13" meant the
> suppression decision in one paragraph and "three of my own tests were vacuous" in the next.
> Two collided outright. Same hazard §0.2 records for the five unrelated `O1`s in this set.

**BD1 — §4.4's single migration is split by owning step.** §4.4 describes one Phase 4 migration
creating five tables. It cannot be built that way: `test_pg_baseline.py::test_baseline_matches_metadata`
compares `Base.metadata` against the migrated schema and fails the moment a model exists without a
migration. The suppression model lands at Step 2 and the outbox at Step 4, so one Step 6 migration
would leave the suite red across four groups. Measured, not predicted — it fired on G2's first run.

So each table ships with its own revision, which is also how SPEC-004 shipped `0010` and `0011`.
`0012_email_suppressions` is G2's. **A21 is therefore green at G2 rather than G6**, and A30 covers
every Phase 4 migration's round-trip rather than one — strictly stronger, since each is exercised
independently.

**BD2 — F.3b gained `--collect`, because the reconciler passed while a node id did not resolve.**
G2's A1/A2 were written into `test_email_service.py`, next to the choke point they test, while §8
declares them in `test_suppression.py`. `scripts/spec005_reconcile.py` compared the DAG against the
spec and reported OK; `pytest tests/unit/test_suppression.py::test_lifecycle_suppressed` answered
*"not found"*. Condition E caught it, one group after the script that exists to catch it.

The same half-a-comparison shape as §0.5, one layer in: the script checked two **documents**
against each other and never asked whether the test it named existed. `--collect` closes it, and
only for files that already exist — a missing file is an unbuilt group, not a defect.

**The lesson, third time in this run:** a check that compares two of the three artifacts will pass
while the third disagrees with both.

**BD3 — the outbox index leads with `account_id`, and `drain` is per account.** §4.1 declares
`Index("ix_email_outbox_due", "next_attempt_at", "sent_at")` and §5.3's `drain(session, *, limit,
now)` takes no account — together, a global "every due row, oldest first" sweep.

**Measured through the app role with the GUC cleared: that query returns zero rows.** The RLS
predicate is `account_id = NULL`, which is NULL rather than true, for every row. So the index would
serve a query that can never return anything, and `test_composite_indexes_lead_with_account_id`
rejects it besides. An `EXPECTED_NON_LEADING` exemption would have been buying an exception for a
query that cannot run.

`drain` binds an account; `drain_all` sweeps accounts — the pattern `cli/jobs.py` already uses for
`reconcile` and `trial-sweep`.

**BD4 — the spec contradicts itself on render timing, and §4.1 wins.** §5.2 orders `_send` as
*"suppression check → render → unsubscribe headers → enqueue"*; §4.1 says the `context` column
holds *"the render CONTEXT, not the rendered html … Rendered at SEND time, not enqueue time, so a
template fix repairs queued mail."* Both cannot hold. The model comment is load-bearing — it is the
reason the column is JSON — so suppression is checked at enqueue and rendering happens in `drain`.

**BD5 — A1 went vacuous when G4 landed, and the mutation check is what found it.** `_send` stops
touching the provider at Step 4, so `test_lifecycle_suppressed`'s `assert provider.calls == []`
held whether or not the suppression check existed. Re-running `mutate_g2.py` after G4 flipped
`_send stops checking suppression` from RED to GREEN — **on a criterion already ticked**.

Diagnosed per conventions §2 as an *untested arm*, not a redundant condition: the enqueue-time
check still decides whether a row is queued at all, and `drain` re-checks because an address can be
suppressed while mail waits. Without an assertion on the queue, the two checks make each other
untestable — remove either and the other covers for it while the outbox fills with mail that will
never send. A1 now asserts both halves.

**The rule this earns: re-run every earlier group's mutations after a group that moves a call
site.** A tick is not permanent; it is a claim about code that later groups can invalidate.

**BD6 — A15's "deployment manifest" is `mihomes cron setup`, and it was already wrong.** The spec
says each workload must be *"registered in the deployment manifest"*. There is no product
`fly.toml` (only the landing app's) and no schedule declared anywhere, so the manifest is
`cli/cron.py` — the one place the repo tells an operator what to schedule.

**It listed four commands, and neither `jobs reconcile` nor `jobs trial-sweep` was among them.**
SPEC-004 added both workloads and neither reached it. Nothing failed; nothing could. That is
precisely the silent drift A15 was written against, having already happened one phase earlier.
`cron setup` now renders from `jobs.SCHEDULE`, and A15 asserts the two agree.

**BD7 — a second instance of SPEC-004's entrypoint bug, found the same way.** `mihomes cron setup`
prints text and reads nothing, but it inherited the root callback's tenant gate — so on any
multi-account install the one command that tells an operator what to schedule exited 1. Found by
A15 *invoking* it; every previous test of its output had constructed the panel directly.

**Two more bugs, both "a job with nothing to do fails rather than reporting zero":**

- `reconcile` constructed the Stripe provider before checking whether any account had a customer,
  so on every install without Stripe configured (§0.8 U6 — that is all of them today) a nightly
  cron line exited 1 with `STRIPE_SECRET_KEY is not set`.
- `drain_all` constructed the email provider **per account**, so a missing `RESEND_API_KEY`
  produced one traceback per account and an empty queue could not be drained at all.

Both now resolve their provider lazily, on first real work. Cron mails a failure every night, and
an operator who learns to filter this job's mail is the actual cost.

**BD8 — A17's node id had to move, and the test it shadowed was a sixth of the criterion.** §8
declares A17 as `test_jobs.py::test_idempotent`; that name existed but was nested in
`TestReconcileIsIdempotent`, so the bare node id did not resolve *and* the test it named covered
one workload of six. Renamed to `test_reconcile_is_idempotent`, and A17 now sits at module level
parametrized over `SCHEDULE`.

**A test that polluted a shared fixture** — worth recording because it passed alone. A17's first
version created a second account in a module fixture; `cli_database` is shared across five CLI
modules for the whole session, so `test_report_upcoming` then failed with *"This install has 2
accounts"*. The second account is now created and removed inside the one test that needs it.

**BD9 — the deletion record takes the mixin's CASCADE, and the first design was wrong.** §5.4
lists `account_deletion_requests` under `PRESERVE`: it is the proof a deletion was honoured, so it
outlives the data it describes. That reads like an argument for a `SET NULL` FK and a nullable
column — and `test_each_tenant_table_has_account_id` rejects exactly that, because every tenant
table's `account_id` must be NOT NULL.

The gate was right and the design was solving an imagined problem. §5.4's purge enumerates
`Base.metadata` **filtered by `TenantOwned`**, and `accounts` is a `GLOBAL_TABLES` entry outside
that sweep — so the purge empties an account's tables and **never deletes the account row**. The
cascade cannot fire. Ordinary `TenantOwned` shape, no override.

Worth stating because the question decided the table's class: had the purge deleted the account
row, `TenantOwned` would have been impossible in any form (CASCADE destroys the proof, RESTRICT
blocks the purge, SET NULL needs the forbidden column) and this would have been the
`processed_webhook_events` shape — `GLOBAL_TABLES`, nullable `account_id`, no FK. Read from §5.4
rather than pattern-matched.

**A mutation that read GREEN because it broke the wrong source of truth.** "Unique constraint
dropped from the enrolment" mutated the *model*; the test asserts the live catalogue after
`upgrade head`, and the migration still declared the constraint — so the database still had it,
and GREEN was the honest answer. Retargeted at the migration. The model/migration disagreement is
`test_baseline_matches_metadata`'s job, not this gate's.

**A30 is stronger than §4.4 asked for.** The single-migration version could only assert one
revision applies and reverts. The split (D1) means `test_migration_phase4.py` enumerates Phase 4's
revisions **from the versions directory** and round-trips them — up, down past the first one, and
up again. The replay is what catches a `downgrade` that leaves an index, policy or trigger behind:
it fails with "already exists" rather than passing quietly. §9 records that no existing test
exercised Alembic at all; this is the first.

**BD11 — A6 caught a real cross-tenant leak, in the export written to satisfy it.** The first
`build_export` read every table with `select(table)` on the Core `Table` object. It returned
**three accounts' properties**.

`tenancy/session.py` applies its tenant filter through `with_loader_criteria`, which binds to a
*mapped class* — a Core `Table` select is invisible to it. The test suite connects as `postgres`,
a superuser, and superusers bypass RLS unconditionally even under `FORCE ROW LEVEL SECURITY`. So
the ORM filter was the only thing filtering, and a Core select had stepped around it.

**Stated precisely: production connects as a non-superuser, so RLS would have caught this.** What
failed is the defence-in-depth layer D14 actually names — *"assembled from the ORM under the
scoped session"* — which is the right finding without inflating it into a live breach.

Mapped classes now go through `select(model)`. The two association tables cannot: they have no
class, so `with_loader_criteria` cannot reach them and RLS is their *only* protection — which,
under a superuser test connection, is none. They are filtered explicitly, and
`test_association_tables_are_filtered_by_account` seeds a foreign row to prove it. Without that
seed the filter was untested: deleting it left all nine tests green.

**An inert mutation, documented rather than left as a puzzle** (conventions §2): routing mapped
classes down the association branch is *also* correct, because that branch filters explicitly. The
leak was never "Core tables", it was "no filter". The branch stands because D14 says ORM, and
because a mapped read that silently stopped being filtered is a bug in the app-wide guarantee that
the export should surface rather than paper over.

**BD10 — Step 7 says "Owner-only route" and no §8 criterion covers it.** A8 is Step 8's, about
deletion. So F.3a would pass on a G7 that shipped only the service. `test_privacy_routes.py` is
the gate that makes the step's own words checkable; it reuses row 16 (`account.delete`) rather
than adding a 21st matrix key, because downloading every row an account holds is the same
authority as ending the account.

**Committed test data broke three tests a module away — the second time this run.** `test_export`'s
seeds commit deliberately (the point is data the test's session could not otherwise see), and
committed rows do not roll back. `test_archive`'s `get_stats` counts `audit_log` across the whole
database and asserted `4 == 2`; `test_trial`'s fixtures assume a known estate. Both seeds are now
context managers that delete what they wrote — including the audit rows the service layer
legitimately produced.

**BD12 — A7 and A28 contradict each other read literally, and the test says which reading wins.**
A7 is *"a purge leaves zero rows in every `TenantOwned` table"*; `account_deletion_requests` is
`TenantOwned` **and** `PRESERVE`, and A29 requires that same row to survive. The two are
consistent only if A7 means the tables the purge *deletes*. Scoped to the `DELETE` disposition,
stated in the test rather than resolved silently.

**BD13 — three of my own tests were green for the wrong reason, each caught by mutation.**

- **A10 asserted zero storage deletions and passed.** The seeder's synthetic `file_path` was not
  a storage key — `is_storage_key` requires the `{account_id}/` prefix — so the purge skipped
  every document and "storage was called" had nothing to be true about. The fixture now builds a
  real key with `build_key`.
- **The ANONYMIZE branch was untestable while empty**, so §6 Step 8's own instruction applies:
  *"it is exercised by a fixture table until SPEC-008 supplies the first real one."* Deleting the
  entire branch left every test green until that fixture test existed.
- **And that test was itself vacuous at first**: it nulled a column the seeder had already left
  NULL, so "is None afterwards" was true beforehand. It now sets the column non-NULL first.

**§5.4's NOT NULL warning arrived exactly on schedule.** Pointing ANONYMIZE at `notes.content`
raised `NotNullViolation` on the first run — *"a NOT NULL column cannot be anonymized, and that is
discovered at implementation time if nobody says so first."* Recorded in
`ANONYMIZED_TABLES`' docstring as measurement rather than caution, because SPEC-008's
`VendorReview` must declare its author columns NULLABLE and now has empirical grounds.

**A29b is derived, because its answer is "none" today.** §5.4: *"There are none today; SPEC-008
adds the first."* An assertion over an empty literal cannot fail. `account_referencing_global_columns()`
discovers the columns from `GLOBAL_TABLES` at run time and `purge` consumes whatever it returns,
so the mechanism exists before the first member arrives rather than after.

**The populated-account fixture deadlocked the suite before it worked.** Committing on a second
connection while the `session` fixture holds an open transaction meant its INSERT into
`account_deletion_requests` blocked the fixture's teardown DELETE on the same table — pytest hung
with no output at all, diagnosed from `pg_stat_activity`. Seeding through the test's own session
removes both the second connection and the teardown.

**C9's open question, answered by measurement: the webhook exemption does NOT cover
`/unsubscribe`.** The harness left it open — *"check whether `WEBHOOK_PATH_PREFIX`'s exemption
already covers it or a second prefix is needed — do not assume either."*

Probed before writing the route: a POST to `/unsubscribe` with `Host: mihomes.ai` returned
**400 Invalid Host**, while `/webhooks/stripe` passed the guard and reached its handler. So the
route carries `UNSUBSCRIBE_PATH_PREFIX`, exempt by the same argument the webhook uses — a mail
client POSTing from its own infrastructure to the public hostname is not a browser the user is
driving, and neither guard's threat model reaches it. CSRF is unweakened: the route reads no
cookie and trusts no caller identity.

**BD14 — the header builder lives in `suppression.py`, not `EmailService`.** The obvious home was
the service, and it was a circular import: `service.py` already imports `outbox.py`, and `drain`
is where the headers must be built (at send time, so the token's lifetime is the message's rather
than the queue's). `suppression.py` already owns the token and both modules import from it.
Measured, not predicted — the first arrangement raised `ImportError` on the first import.

**Two things the route gets deliberately right, both from N-rules:**

- **The GET renders a form and suppresses nothing** (N10's corollary). Mail clients and scanners
  prefetch links; a prefetched GET that unsubscribed would opt out people who never clicked.
  RFC 8058's one-click path is the POST.
- **Both query parameters are HTML-escaped.** They arrive unauthenticated and land in HTML
  attributes — the textbook reflection shape. That a legitimate caller only sends an address and
  a hex digest says nothing about what an attacker sends.

**A node id in the DAG did not resolve.** G9.2's verify cell named
`test_route_declarations.py::test_every_allowlisted_module_names_its_mechanism`; the test is
nested in `TestAllowlistDiscipline`. Corrected in the DAG rather than by moving the test — this
one is not an §8 criterion, so `--collect` does not check it, which is exactly why it slipped.

**BD15 — §5.2 lists `send_dunning` as lifecycle. It ships transactional.**

§5.2's grouping is explicit: *"Lifecycle mail — every one of these is `klass="lifecycle"`"*, with
`send_dunning` under it. That contradicts D13's own criterion — *"a receipt for money taken is not
marketing and must send regardless of unsubscribe state; a drip is, and must not"* — and it
contradicts this run: SPEC-005 G2 classified rung 1, SPEC-004's `payment_failed`, as
**transactional**, with the reason inline.

The discriminating question is whether a **suppressed** address needs rungs 2–4. It does. Under
D13 suppression is absolute for lifecycle mail, so an unsubscribed customer would be told once
that their card had failed and then silenced while their access lapsed — the exact failure G2's
reasoning names. A18 would also put `List-Unsubscribe` on "your payment failed", which G9's own
docstring calls wrong and costly.

Rungs 2–4 are rung 1 escalating about the same unpaid invoice. **One sequence, one class**, and
the two could not have differed whichever way it went.

**BD16 — Step 10's third verify clause has no §8 criterion.** *"The ladder never outlives the
subscription that started it"* is neither A23 (the schedule) nor A24 (recovery), so F.3a would
have passed on a Step 10 that shipped without it — the same gap G7's owner-only route had. Built
rather than deferred: `subscription.cancelled` joins `RECOVERY_EVENT_TYPES`, because two more
weeks of "update your card" after someone has cancelled is dunning a person who is no longer a
customer.

**No new table, and that is the design.** §4.4's five tables are all shipped and none is a
sequence table; N12 forbids an `accounts` migration. `EmailOutbox` already carries
`next_attempt_at`, `klass`, `template` and `context` — a row due in seven days **is** a scheduled
send. So the ladder enqueues four rows and `drain-outbox` sends each as it comes due, which makes
A23 close to structural rather than something the ladder enforces.

`next_attempt_at` does double duty: it is also the backoff field, so a rung due in seven days that
then fails delivery is rescheduled to +1 minute. That composes correctly — dunning picks the first
attempt, backoff owns retries after it — and is commented, because two schedules sharing a column
reads as a conflict.

**The seam SPEC-004 left open.** `send_payment_failed` existed with **no caller**: no email was
wired to any billing event at all. A23 assumes "a single `invoice.payment_failed` produces one
email", so G10 had to build both halves — the ladder and the webhook hook that starts it.

**BD17 — "enrolment on account creation" has no §8 criterion, and it is the load-bearing half.**
A25 is *"a drip sends each step once and never twice"*, which a drip system with **zero
enrolments** satisfies perfectly: nothing sent, nothing sent twice. Step 11 also says *"enrolment
on account creation"*, and nothing in §8 covers whether an account is ever enrolled.

**Third instance of this shape in one phase**, after G7's owner-only route and G10's third verify
clause. Wired into `create_account_step` with its own test rather than recorded as a gap — the
seam is one line, and the alternative is a mechanism nothing ever starts.

Failures there are swallowed: account creation is the one irreversible step in onboarding, and a
marketing sequence must never be able to fail it. Mutation-checked in both directions.

**The sequences are a module constant, not `configurations` rows.** `configurations` is a
**tenant** table and a drip sequence is product-wide, so storing it there means a row per account
drifting apart silently, with no answer to "what does the onboarding sequence say" that is true
for everyone. Declared as data with placeholder templates, the same shape as `BACKOFF_LADDER` and
the dunning `LADDER`. O1 replaces the copy and the intervals; **the mechanism does not change**,
which is what makes the openness harmless (conventions §3.3).

**Step 11's verify cites A22, which is about something else.** A22's declared test is
`test_suppression.py::test_idempotent` — suppressing an address twice is a no-op — and it is
already green from G2. The clause Step 11 means is *"a suppressed address receives nothing"*,
which is **A1**'s, enforced at the choke point. Asserted here under its own name rather than
re-gating A22, because a drip is the canonical lifecycle message and the one D13 was written
about.

**A test of mine asserted the tenant layer working and called it a bug.** The enrolment test read
`CampaignEnrolment` on the `session` fixture's binding while `create_account_step` had created a
*different* account — so the ORM filter correctly hid the row, and the test reported
`NoResultFound` on an enrolment that existed. Probed to distinguish "not created" from "not
visible": the raw table held it, the scoped session saw zero. The test now reads under the new
account's context.

**BD18 — Step 12 was already "done", and none of it worked. Four defects, all measured.**

The tree arrived at G12 with `audit.py`'s module docstring already reading *"Step 12 (SPEC-005
Phase 4): gated on Estate plan"*. Every part of that claim was false in a different way.

1. **The `predictive_maintenance` gate allowed every plan.** It passed
   `check_entitlement(account, "predictive_maintenance")` — an entitlement **key** where `can()`
   keys `_BOOLEAN_ACTIONS` on **actions**. The string matched neither `_BOOLEAN_ACTIONS` nor
   `_COUNTED_ACTIONS`, fell through to `can()`'s closing `return Allowed()`, and returned Allowed
   for Free. The action is `maintenance.predict`. **The spec's own §5.5 carries the same wrong
   string** (`predictive_maintenance.run`), as did the abandoned pre-G12 work in the stash — so
   an A12 written from the spec verbatim would have passed vacuously against a dead gate.

2. **`PLAN_LIMITS["pro"]["predictive_maintenance"]` was `True`.** `PRICING:89-91` writes all three
   Estate keys `false | false | true`, and D10 is *"enforced exactly as `PRICING` §3.1 writes
   them"*. **§0.6 recorded these three keys as ✅ verified `False` on Free *and Pro*** — a
   pre-flight verification that passed while the tree disagreed with it. Third instance of §0.5's
   shape in this run, and the second to land in the *checking* rather than the code.

3. **`can()` and `_upgrade_target` defaulted to different tables.** `can()` resolves through
   `limits_for`, defaulting to `PLAN_LIMITS`; `_upgrade_target` defaulted to
   `PLAN_LIMITS_PHASE3`, whose overrides granted `audit_export` to free **and** pro. Measured: a
   Free account denied `audit.export` was told to upgrade to **Pro, which also denies it** —
   exactly the failure `_upgrade_target`'s docstring exists to prevent, arriving through the
   defaults rather than through the walk. Fixed at the **default** (the class of bug) rather than
   by deleting the override (this instance); the override was emptied as well, because it
   contradicted `PRICING:91` and nothing had rolled it out.

4. **`audit.export` swallowed its denial and returned `[]`.** An empty list is indistinguishable
   from "this account has no audit rows", so a Pro owner saw a working, empty export rather than
   a paywall — and the `upgrade_target` was discarded, which A34 needs at the route and CLI.
   Now raises **`EntitlementDenied`**, a new `PermissionError` subclass carrying the `Denied`
   intact. **G14 depends on this**: built on a bare `PermissionError`, A34 would have been
   unreachable. No caller outside the module used `export`; the 20 services that import from
   `audit` all take `record_change`/`diff_instance`/`snapshot_instance`.

Also deleted an unreachable block in `can()` after the first `_BOOLEAN_ACTIONS` return — it read
`plan` instead of `plan_name` and built `Denied(code=…, message=…)` against a three-field
dataclass, so it would have raised `TypeError` had control ever reached it.

**Two acceptance-adjacent tests edited, named because that should be visible.**
`test_entitlements.py::test_upgrade_target_skips_a_plan_that_would_also_deny` and
`test_limits.py::test_an_estate_only_key_points_past_pro` both asserted `upgrade_target == "pro"`.
Their names and docstrings argue *for* the fix — "an estate-only key", "skips a plan that would
also deny" — and only the expected value was wrong: they were pinning defect 2. Both now assert
`"estate"`, and the second gained the round-trip the docstring is really about (*the plan named
must actually allow it*), which is the assertion that catches defect 3 rather than restating it.

**A13 asserts by execution, not by grep.** It calls `record_change` under every plan, then
monkeypatches both `can` and `check_entitlement` to raise. A gate introduced through a helper, a
decorator or an import alias survives a text search and fails that.

**Two of my own test bugs, both caught by running it.** `entity_id` is a UUID column since
SPEC-002 D2's int→UUID remap, so a synthetic `1` was rejected by the *database* rather than by
the gate — a failure that reads like A13 breaking when nothing about entitlements had changed.
And `audit_log.action` is `varchar(10)`, so `"updated-on-free"` truncated. Neither was visible by
reading either file.

## 3. Circuit breaker (conventions §3)

Halt and write the report with status `HALTED` if: more than **5** tasks poison, **or** G6 poisons
(nothing from G7 onward can be built without its tables), **or** G5 poisons (three workloads depend
on it), **or** two consecutive groups fail their full-suite gate.
