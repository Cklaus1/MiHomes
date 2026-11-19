# SPEC-006 Build Loop — Gateways: tenancy, webhook transport, WhatsApp Cloud API

> **Input spec:** `docs/specs/SPEC-006-gateways-tenancy-webhook-cloud-api.md` (752 lines,
> *Ready to build*, **2 open decisions** — O1 Cloud API tier + group support, O2 webhook host for
> local installs)
> **Conventions:** `tasks/build-loop-conventions.md` — stop condition, poison ceiling, circuit
> breaker, artifact routing are defined there and inherited here **unchanged**.
> **Branch:** `worktree-spec-build-harness`. **Target ref:** HEAD `a03f712` (SPEC-005 complete).
> **Invocation:** `/loop tasks/build-loop-spec006.md`
> **Status: COMPLETE 2026-08-30.** 25/25 criteria green, suite 2538 passed. G9 blocked by U5
> and shipped as ratchets. See `tasks/build-loop-spec006-report.md`.

**The stake, in the spec's own words:**

> **A gateway with no tenancy does not fail closed, it fails into the wrong account.** An unlinked
> sender whose message lands in whichever account the process happens to be configured for is a
> cross-tenant write that looks exactly like the feature working — the same shape as SPEC-005's
> export defect, arriving through a channel where nobody is watching a screen.

The gateways are how housekeepers, groundskeepers and contractors actually use this product; most
will never open the web app. Today every one of them talks to a single-tenant process.

**Exit criterion:** A25 — a linked sender in account A, arriving by webhook with no poller
running, delivered through the Cloud API, creates a row in A and nothing in B.

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, all four DB env vars set | ✅ PostgreSQL 18.x, `mihomes_test` + `mihomes_phase0` |
| P2 | `MIHOMES_SECRET_KEY` set | ✅ required since SPEC-003 U1 |
| P3 | The §2 doc repairs (B1–B10) | ✅ spec §6 records these as landed in the spec's own commit. **A1 is the regression gate**, not the work |
| P4 | The shared responder core — `review_common.py`, `dedup.py`, `pid.py` | ✅ measured, §0.6 |
| P5 | `telegram_links` table with `account_id` + composite unique | ✅ `0007_telegram_links.py`. **N9's stop condition does not fire** — see §0.3 |
| P6 | **`telegram-bot` reconciled with `origin/main`** | ❌ **NOT DONE, AND NOT THIS RUN'S JOB** — §0.2 |
| P7 | A Meta/WhatsApp Cloud API account + verified number | ❌ absent. Blocks the *live* half of Steps 7/9 only; O1 also unresolved |

**Environment — pass inline, the worktree guard rejects `export` chains. Set as separate
PowerShell statements, not a chain:**

```
DATABASE_URL               postgresql+psycopg://postgres@localhost:5432/mihomes_test
MIGRATION_DATABASE_URL     postgresql+psycopg://postgres@localhost:5432/mihomes_test
TEST_DATABASE_URL          postgresql+psycopg://postgres@localhost:5432/mihomes_test
LANDING_TEST_DATABASE_URL  postgresql+psycopg://postgres@localhost:5432/mihomes_phase0
MIHOMES_SECRET_KEY         <Fernet key — `mihomes config generate-key`>
```

Invoke pytest as `py -m pytest`, never `python`. Use `--color=no` when parsing output.
**Without these, DB-backed tests self-skip and the suite reads green** — six SPEC-005 files alone
produced 26 skips that way. Conventions §0 makes a new skip red for exactly this reason.

---

## 0.2 P6 is unmet, and this harness does not discharge it

**`todo.md` names SPEC-006 as blocked on an unowned human decision: reconcile `telegram-bot` with
`origin/main`.** Measured at authoring time: **30 ahead / 13 behind**. Nobody owns it. The spec
says so itself (§0.1, §10) and states the consequence in the strongest terms available:

> **If this spec is built from `telegram-bot`, everything in §3–§5 is wrong**, because it assumes
> a core that branch lacks.

**The pre-flight found the core *is* present on this branch** (§0.6 — `review_common.py` at 1180
lines, both target signatures matching §5.3 exactly, the 15-category superset, the dead
`WhatsAppBridge` Protocol, all three dedup primitives, six gateway test files). So the *code*
precondition P2-of-the-spec describes happens to hold here.

**That is not the same as the branches being reconciled, and the distinction must not be quietly
collapsed.** What holds is "the modules §3–§5 consume exist on `worktree-spec-build-harness`".
What remains open is "`telegram-bot` and `origin/main` tell one story about the gateway code" —
a merge decision with 30 commits of unrelated work on one side, which is a human call about
product history, not something a build loop can measure its way through.

**Consequence for this run:** the build may proceed on this branch, because every module it
consumes was verified present here by execution rather than assumption. **The reconciliation stays
open and is carried in §0.8 as U1.** A future session must not read "SPEC-006 built successfully"
as "the branch question was answered".

---

## 0.3 N9's stop condition was checked, and it does not fire

The spec's N9 is a halt instruction, not a caution:

> **Do not add a `telegram_links` migration.** SPEC-003 §4.2 pre-ships it with `account_id` and the
> composite unique constraint. **Needing one here means SPEC-003 diverged — stop and reconcile.**

**Measured before writing any harness content.** `alembic/versions/0007_telegram_links.py` creates
the table, and carries: `account_id` (from `TenantOwned`), `UniqueConstraint("account_id",
"telegram_user_id")`, `ondelete="CASCADE"` on `membership_id` → `memberships.id`, RLS via
`policy_statements()`, drift-guard triggers via `trigger_ddl_statements()`, and
`ix_telegram_links_lookup` on `telegram_user_id` **deliberately not led by `account_id`** — the
model's own comment explains why: *"the bot resolves a sender before it knows which account they
belong to — that resolution is how the account is discovered."*

That index comment is worth carrying forward: it means **SPEC-003 already anticipated §5.1's
unscoped lookup** and shaped the schema for it. D11/D12 are being built on a table designed for
them, which is the cheapest thing in this phase and the reason Step 1 adds only the *token* table.

**No divergence. No stop. Step 1 adds `gateway_link_tokens` and nothing else.**

---

## 0.4 Stop condition

Per conventions §0, all five. Conventions §0.1: *"SPEC-003 onward — C is suite green **including
this spec's new tests**."*

| | Condition | For SPEC-006 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 below |
| **B** | every §6 step tasked **and** every §8 criterion gated | F.3a + F.3b |
| **C** | full suite green **including this spec's new tests** | baseline below → final |
| **D** | smoke green | `tests/integration/test_smoke_all_tools.py` |
| **E** | every §8 criterion green by its own named test | **all 25**, F.2 |

**Baseline to beat — HEAD `a03f712`, correct env, before any SPEC-006 code:**

```
py -m pytest -q   →   2405 passed, 3 skipped, 2 xfailed, 0 failed   (~330s)
```

The 3 skips and 2 xfails are inherited and known-benign. **A new skip is red.**

### 0.5 Condition E has four holes this spec cannot close by itself

Conventions §0: *"a stub can satisfy A+B+C+D."* Four SPEC-006 criteria define their own scope and
would pass vacuously on a faithful implementation. Each gets a **derived** gate — the construction
used six times across SPEC-003/004/005 (`UNFILTERED_CLASSES`, `G-census`, `G-dispatch`,
`ALLOWLIST_MECHANISMS`, `G-purge`, `G-export`).

| Gate | Check | Closes |
|---|---|---|
| **G-branches** | A11 enumerates `REVIEW_SCHEMA`'s categories **from the tree** (15) and asserts each *writing* branch (14 — see C9) writes only within the resolved account. A sixteenth category added without scoping fails the suite, **and so does a branch that silently stops writing**. | A11 — §9 states this explicitly: *"enumerate by walking the tree… must fail when someone adds a fifteenth category without scoping it"* |
| **G-coverage** | A24 derives the module list from the `omit` globs and the gateway package, not from a hand-written list. | A24 — a transcribed list is green the day it is written |
| **G-baileys** | A22 sweeps `src/` + `scripts/` for imports of the Baileys client, derived from the module path rather than a literal string. | A22 — an import added later under a different alias |
| **G-refusals** | A8's four refusal modes assert **four distinct messages**, not merely that each raised. A single generic `ValueError` satisfies "each is refused" while telling a locked-out user nothing. | A8 — the spec says *"each refused with a distinct message"*; "raised" is the vacuous reading |

**A11 is the phase's definition of done**, and the spec says why in terms that belong in the
harness rather than only in the spec:

> Every other criterion here can pass while A11 fails, and the symptom is a row appearing in a
> stranger's estate with a cheerful confirmation sent back to the person who caused it. **Nobody is
> watching a screen when it happens.**

### 0.5b The negative-assertion rule, carried from SPEC-005 BD19

Six SPEC-005 tests were green with the feature deleted. The rule that run earned, restated as a
check rather than a caution:

> **When an assertion is negative — did not raise, was not called, is None, no row appeared — ask
> what would make it vacuously true, and make the positive case a parameter of the same test.**

SPEC-006 is unusually exposed to this: **A6, A11, A12, A14, A22, A25 are all negative assertions.**
"B sees nothing" is true when nothing was written at all. Every one of them must pair its negative
with a positive in the same test — A's row *did* appear, the trusted sender *was* matched, the
valid signature *did* reach the handler. A11 without a populated `account_b` is meaningless.

---

## 0.6 PRE-FLIGHT RE-VERIFICATION (conventions §3.1) — measured at HEAD `a03f712`, 2026-08-29

**SPEC-006 was verified against `origin/main @ be8d398` on 2026-07-30 — a ref that predates all
five built phases.** Conventions §3.1 forbids proceeding on an unverified premise in **either**
direction, so all eleven §1.5 findings were re-measured here.

### Findings that hold — verified by execution, not assumed

| # | Finding | Measured on this branch |
|---|---|---|
| **F3** | `whatsapp/protocol.py` is a dead Protocol shaped like the Cloud API | ✅ `WhatsAppBridge` at `protocol.py:6`; every other hit is `WhatsAppBridgeError`, a different symbol. **Zero implementers.** D10 stands — this is the cheapest thing in the phase |
| **F4** | The shared core exists and is substantial | ✅ `review_common.py` **1180** lines (spec said 1175 — it grew by 5), `GatewayAdapter`, `analyze_messages`, `dispatch_items`, `is_trusted_sender` all present |
| **F5** | The category asymmetry is resolved — one superset | ✅ **15 categories** in one `REVIEW_SCHEMA` enum; both `review.py` files are **16 lines**, thin re-exports. N13 stands |
| **F6** | The watchdog supervises both gateways | ✅ **23** WhatsApp references in `scripts/watchdog.py` — the spec's number exactly. D15 stands |
| **F7** | Redelivery is already idempotent | ✅ `ProcessedIdStore:32`, `PoisonGuard:77`, `poll_lease:145`. D14/N6 build on real machinery |
| **F8** | `is_trusted_sender` resolves to staff, not to an account | ✅ `review_common.py:688`, signature `(session, message, *, gateway)` — **exactly the shape §5.3 widens.** Its docstring already says *"an empty allowlist does NOT trust everyone"*, which is the fail-closed precedent D12 follows |
| **F9** | `notify_staff` is WhatsApp-only; `notify_approver` has the fallback | ✅ `staff_pto.py:229` returns False on no `whatsapp_phone` and imports `WhatsAppClient` directly; `:186`'s docstring documents the H35 fix it never received. **A real live silent failure** |
| **F11** | Six gateway test files exist | ✅ six — `test_gateway_review_common`, `test_gateway_safety`, `test_gateway_property_resolution`, `test_gateway_stop`, `test_telegram_client`, `test_whatsapp_drain`. (A first grep for `test_gateway*` found four; the other two are named for their subject, not the package) |
| **§5.3** | `dispatch_items` already takes `property_slug` keyword-only | ✅ `review_common.py:728` — the new `account` kwarg follows an established shape rather than introducing one |

### Findings CORRECTED — build against the right-hand column

| # | Claim | Spec | **Measured at HEAD** |
|---|---|---|---|
| **C1** | "**Zero tenancy anywhere in `src/`** — `git grep -l account_id` → zero matches, not merely in gateways but in the entire package" | F1 | **False, and structurally so. 91 files carry `account_id`.** `can()` is live at `entitlements/service.py:111`; `require_permission` at `authz/permissions.py:106`; `telegram_links` is in `TENANT_TABLES`. **Every §5 signature the spec wrote "against SPEC-002/003's design" now describes real code.** A builder trusting F1 would re-invent a tenancy layer that exists |
| **C2** | "**Zero webhooks** — `git grep -c setWebhook` → zero" | F2 | **Half false, and the half that matters is the useful half.** No `setWebhook` anywhere ✅ — Telegram is still short-polling. But `web/routes/webhooks.py` **exists** (SPEC-004's Stripe route) with a raw-body signature check, a `PERMANENT_ALLOWLIST` entry and a Host-guard exemption. **Step 5 extends a proven pattern rather than inventing one** — and D7/N4's "raw bytes before any parse" already has a working precedent to copy |
| **C3** | "the gateway responders are excluded from coverage" | F10 | ✅ holds, **and the omit list is now larger than the spec knew** — later phases added entries. A24 must derive the list (§0.5 G-coverage), not diff against the spec's snapshot |
| **C4** | §8 A2 names `test_gateway_review_common.py::test_superset_schema` | §8 | **The node id does not resolve.** The test is `test_schema_enum_is_superset`. See C6 |
| **C5** | §8 A12 names `test_gateway_safety.py::test_trust_is_account_scoped` | §8 | **Does not exist.** The file has `test_is_trusted_sender_matches_staff` / `_rejects_stranger`; A12's test is **new**, extending that file |
| **C6** | §8 A13/A21 name `test_unchanged_under_tenancy` / `test_notify_staff_fallback` | §8 | **Neither exists.** `test_gateway_property_resolution.py` has three `TestResolveDefaultProperty` methods; `test_staff_pto.py` has `TestNotifyStaff::test_returns_false_when_no_whatsapp` |

### C6 — three of the four "existing file, extended" node ids are wrong, and that is the harness's first finding

§8 marks A2, A12, A13 and A21 as landing in **existing** files. Ran `--collect-only` against all
four before writing a line:

| Criterion | §8 declares | **Actually exists** |
|---|---|---|
| A2 | `test_superset_schema` | `test_schema_enum_is_superset` |
| A12 | `test_trust_is_account_scoped` | ❌ new — file has `test_is_trusted_sender_matches_staff` |
| A13 | `test_unchanged_under_tenancy` | ❌ new — file has `TestResolveDefaultProperty::*` |
| A21 | `test_notify_staff_fallback` | ❌ new — file has `TestNotifyStaff::test_returns_false_when_no_whatsapp` |

**A2 is the dangerous one.** It is the only one the spec claims is *already green* — P2's
verification step is *"the six existing gateway test files pass (A2)"*. A harness that trusted §8
would have gated the prerequisite on a node id that does not resolve, and `pytest` would have
answered "not found" rather than red. **This is SPEC-005 BD2 arriving before the first commit**,
which is why `--collect` is not optional here.

**Resolution:** A2 gates at **G0.1** against the real name. A12/A13/A21 are new tests in existing
files, which is what §9's *"extend, do not replace"* already asks for — only the names were wrong.

### C9 — "14 branches" and "15 categories" are both right, and A11 needs both numbers

The spec says `dispatch_items` has **14 category branches** (D11, N3); `REVIEW_SCHEMA`'s enum has
**15** members; §9 says *"a fifteenth category"*. Measured rather than picked:

```
enum members : 15
handled      : 14
NOT handled  : ['informational']
```

`informational` falls through `review_common.py:1091`'s guard and **writes nothing** — correctly,
it is the "nothing to do" category. So both figures are accurate and count different things.

**Why this matters for the phase's definition of done:** an A11 that walks all 15 and asserts
*"nothing appeared in account B"* has **one arm that is vacuously true** — `informational` writes
nothing in *either* account. That is §0.5b's own rule firing on A11 itself.

**So G-branches enumerates from the enum and asserts the split**: 15 categories, of which exactly
14 write, each within the resolved account only. A category that silently stops writing then turns
the gate red instead of quietly joining `informational` — which is the regression a
count-of-branches test would never see.

### C10 — write the three pending tests at MODULE level, deliberately

§8 declares bare node ids (`test_staff_pto.py::test_notify_staff_fallback`) but two of the three
files put **every** test inside a class:

| File | Convention | §8 declares |
|---|---|---|
| `test_staff_pto.py` | all nested — `TestNotifyStaff::`, `TestApprovePTO::`, … | bare `test_notify_staff_fallback` |
| `test_gateway_property_resolution.py` | all nested — `TestResolveDefaultProperty::` | bare `test_unchanged_under_tenancy` |
| `test_gateway_safety.py` | flat | bare `test_trust_is_account_scoped` ✅ |

**A builder following each file's local convention writes a nested test, and the node id does not
resolve** — BD6 again, in the two places most likely to invite it.

**And the expiry test would not catch it**, which is why this is a C-row and not a footnote:
`test_a_pending_test_that_now_exists_must_be_removed` asserts the node id does *not* resolve. A
test written under a nested name does not resolve either — so the pending entry survives, the group
reads as landed, and that criterion is **permanently exempt from `--collect`**. Exactly M5's
failure arriving through a door M5 does not cover.

Write both at module level. The file's class convention loses here, because §8's node id is what
`--collect` enforces.

### C7 — labels are derived, never range-checked

Conventions §4.2. SPEC-006's set is **`A1`–`A25`, plain, no lettered members** — measured, not
assumed. A range check would work *today*, which is exactly the trap: the numeric set being
gapless is not a property the next edit preserves. `scripts/spec006_reconcile.py` parses
`A\d+[a-z]?` and joins §8 → §9 → §1, and **runs after every group commit**, not once at G-Final.

### C8 — SPEC-006's fail-closed inheritance, and the one gate that will fire

This phase adds **one table** (`gateway_link_tokens`), so C8 is smaller than SPEC-005's five. Each
gate must still be answered:

| Gate | What it demands |
|---|---|
| `test_matrix.py::test_every_model_is_classified` | one `ENTITY_CLASSES` entry for `GatewayLinkToken` |
| `tenancy/registry.py::check_registry` | `gateway_link_tokens` in `TENANT_TABLES` |
| `test_baseline_matches_metadata` | UUID PK, `DateTime(timezone=True)` matched to the mixin |
| **three pinned counts** | `test_pg_baseline.py`, `test_tenancy_registry.py`, `test_isolation.py::EXPECTED_TENANT_TABLE_COUNT` each +1 |
| `test_u7_enforcement.py` | **expect this to fire.** A new `ACCOUNT_LEVEL` model with no property linkage lands in the denied-outright set — and that is *correct* here: a link token is account-level administration, not something staff read |
| `test_composite_indexes_lead_with_account_id` | **the token table's unique is on `token_hash` alone.** Redemption looks a token up before any account is known (§4.2's carve-out), the same argument `telegram_links`' lookup index already won. Budget for an `EXPECTED_NON_LEADING` entry **with that reason written down** |

**The §4.1 model as specified uses `String(36)` PKs. This repo uses `PGUUID(as_uuid=True)`** —
`telegram_link.py` and every SPEC-003+ model. Follow the repo (N9's spirit: matching the shipped
pattern), and record it as a deviation.

---

## 0.7 O1 and O2 — both blocks-ship, neither blocks the whole build

Conventions §3.3. The spec has already done the split for O1 and half of it for O2:

- **O1** (Cloud API tier + group support) — *"blocks **the WhatsApp half of Step 8 only**. The
  Protocol (D10), the adapter, and the webhook are tier-independent."* Build the tier-independent
  parts; A18/A19/A20 gate them. **Whether group JIDs survive is a founder cost/capability call.**
- **O2** (webhook host for local installs) — *"blocks **the cutover half of Step 6**. The route,
  verification and handler are identical either way."* Build route + verification + handler; the
  cutover decision is recorded, not made.

**Do not route either to the user mid-run.** Both are carried in §0.8.

## 0.8 UNMET LAUNCH GATES — carried forward, not silently satisfied

| # | What | Owner |
|---|---|---|
| **U1** | **`telegram-bot` ↔ `origin/main` unreconciled** — 30 ahead / 13 behind. The spec's own P2. This run proceeds because the consumed modules were verified present *here* (§0.2), which is not the same thing | founder |
| **U2** | **O1 — Cloud API tier and group support.** The live product routes an inventory *group* (`whatsapp.inventory_group_jid`); a tier without group support makes this a **loss of function**, not a transport swap. **G7 built every tier-independent part and A18–A20 prove them**; the question is surfaced at runtime as `GroupsNotSupported`, which names the routing key that needs replacing rather than degrading silently. `supports_groups` defaults to False, so an unconfigured install refuses loudly instead of discovering the limitation in production. What remains is the founder's cost/capability decision, and — if groups are dropped — a replacement routing concept for the inventory group | founder |
| **U3** | **O2 — where the webhook terminates for a local install.** Determines whether D14's poller fallback is temporary or permanent. **Discharged as far as the repo can: G6 shipped route, verification and handler, and A14–A17 prove them.** What remains is not code — a local install has no public hostname and no TLS, so the webhook needs a tunnel, a relay, or a hosted deployment, and choosing among those is a product decision about how MiHomes is distributed. Until it is made, `cli/telegram.py monitor` stays runnable and is **now marked deprecated in its own help text**, naming this gate. Concretely still needed: `setWebhook` is never called by any code here — registration is a deploy-time action nobody owns yet, and `TELEGRAM_WEBHOOK_SECRET` must be set wherever it is called from | founder |
| **U4** | **No Meta/WhatsApp Cloud API account.** Steps 7/9's *live* behaviour is unprovable here; `FakeCloudClient` covers the rest | founder |
| **U5** | **N10 forbids deleting Baileys before Step 7 is green *in production*.** No production exists for it yet, so **A22/A23 cannot honestly be discharged by this run** — see §1 G9 | founder |
| **U6** | **One bot token serves every account.** Per-account tokens are deferred (§7). A compromised token is now a **cross-account** incident — the blast radius grew when tenancy arrived, and nothing here shrinks it | accepted, Phase 4+ |
| **U7** | **A link code is a bearer credential with no second factor.** Hashed, single-use and short-lived — but a code forwarded before redemption binds the wrong person | accepted |
| **U8** | **`cloud_client.py` stays coverage-omitted** after Step 10, on the same network-bound reasoning as every HTTP client here. Its error handling is exercised by nothing in CI | accepted |
| **U9** | Everything SPEC-005 §10 shipped GA with, unchanged — ToS/Privacy, real prices, verified sending domain, alerting. **This spec adds gateways to that list rather than subtracting from it** | founder |

---

## 1. Task DAG

Conventions §1.3: **one step per group by default**; the group commit is the resume point.
Format is conventions §4: `checkbox + ID · spec-ref · criteria · imperative · verify:`.

**The criteria and verify columns are derived, not typed** (§0.5, C7).
`py scripts/spec006_reconcile.py --collect` joins §8 (label → test) with §9 (test → directory)
against this table and exits non-zero on any disagreement. **Run it after every group commit.**

**Ordering constraints the spec names as load-bearing:** **Step 2 before Step 3** (identity
resolves before anything is scoped by it); **Step 4 before Step 5** (tenancy works on the transport
that already works, so a failure is not confused with a webhook bug); **Step 7 before Step 9** (the
Cloud API is proven before Baileys — today's only working WhatsApp transport — is deleted).

### [x] G0 — P4/P5 verification and the A2 correction — *dep: none*
- [x] G0.1 · §6 P2 · A2 · confirm the shared core is present and its six test files green; **gate against the real node id** — §8's `test_superset_schema` does not exist (C6) · verify: `tests/integration/test_gateway_review_common.py::test_schema_enum_is_superset`
- [x] G0.2 · §2 · A1 · the §2 doc repairs are a **regression gate**, not work — B1–B10 landed in the spec's own commit; assert the stale strings stay gone and both PRDs stay indexed · verify: `tests/unit/test_docs_gateway_prds.py::test_repairs_landed`

### [x] G1 — Step 1: the link-token table — *dep: G0*
- [x] G1.1 · §6 Step 1 · A3 · `GatewayLinkToken` + migration, RLS included; **`PGUUID` not `String(36)`** (C8), and no `telegram_links` DDL (N9, §0.3); its own engine running real Alembic up→down→up · verify: `tests/integration/test_migration_gateway_links.py::test_up_down`
- [x] G1.2 · §6 Step 1 · A4 · **hash only, never the raw code** (N8) — a raw token reaches neither the table nor a log record · verify: `tests/unit/test_linking.py::test_token_hashed_only`
- [x] G1.3 · C8 · — · one `ENTITY_CLASSES` entry, `TENANT_TABLES` entry, **three pinned counts +1**; **the predicted `EXPECTED_NON_LEADING` entry is NOT added — see D2 below** · verify: `tests/unit/test_matrix.py::TestEntityClassification::test_every_model_is_classified`

**G1 deviations — measured, not assumed.**

| # | What the spec/harness said | What was measured | Resolution |
|---|---|---|---|
| **D1** | §4.1/§4.2 declare `String(36)` PKs and FKs | `memberships.id` is `PGUUID`; every SPEC-003+ model uses it. A `String(36)` FK does not build | `PGUUID(as_uuid=True)` throughout, as C8 already anticipated |
| **D2** | C8: *"budget for an `EXPECTED_NON_LEADING` entry"* for the `token_hash` unique | **The prediction is wrong.** A `UniqueConstraint` in `__table_args__` emits a *constraint*, not an index — verified on `TelegramLink`, whose unique lands in `table.constraints` while `table.indexes` holds only the two real indexes. `test_tenant_indexes._tenant_indexes()` iterates `table.indexes`, so it never sees the unique, and an entry would be **stale on arrival** and fail `test_every_declared_exception_still_exists`. The invite precedent needed one only because `invite.py:43` declares `unique=True, index=True`, which *does* emit an index | **No entry added.** The unique stays on `token_hash` alone for C8's stated reason (redemption resolves a token before an account is known). Confirmed: all 87 matrix/registry/index gates green |
| **D3** | C8: *"`test_u7_enforcement.py` — **expect this to fire**"* | **Not a deviation — C8 was right.** An earlier draft of this row claimed the file did not exist; that was a search error (`ls tests/unit/` — the file is in `tests/integration/`). It fired on the full suite exactly as predicted: `TestModelsWithNoPropertyLinkageAreDenied::test_the_no_linkage_group_is_exactly_the_models_with_neither_shape`, one extra item, `GatewayLinkToken` | `GatewayLinkToken` added to the denied-outright set **with its reason**, as that gate demands. Correct outcome: a link code is account-level administration, and there is no staff-facing read of this table at all |
| **D4** | §4.2's DDL omits the `membership_id` FK | Without it there is no `ondelete="CASCADE"`, and G3.3's A10 would fall to application code | FK created in `0016`, matching `telegram_links` |
| **D5** | §5.2: `issue_link_token(session, account: Account, ...)` | Written as `account_id: uuid.UUID` — the row stores an id, and nothing in issuance reads the `Account` object. Recorded because **G4.1's A11 requires `dispatch_items` take `account: Account`, required and never defaulted**, so the two signatures will sit side by side looking inconsistent unless the difference is deliberate | Kept as `account_id`. A11's requirement is about *never defaulting* the tenancy argument, which this satisfies — the id is positional and required. Revisit at G3 if `redeem_link_token` needs the object |

**G1 also added three `PENDING_TESTS_IN_EXISTING_FILES` entries** (`G3.1`/`G3.2`/`G3.3`) — a
*third* shape the set had not carried before. The existing three are all "the file predates
SPEC-006"; these are "**G1 created the file G3 writes into**", because §8 puts A4 and G3's three
tests in the same basename. Delete them when G3 lands; `TestPendingSetExpires` turns red if they
outlive it.

**G2 deviation.**

| # | What was found | Resolution |
|---|---|---|
| **D6** | **`telegram_link_service.resolve_sender` already exists** (SPEC-003 §6 Step 16) with the same name and a **conflicting** unlinked behaviour: it returns `None` → `UNLINKED_ROLE = "staff"` under SPEC-003 **D16**, where A6 requires a raise. SPEC-006 never mentions D16 or that module — `grep` returns zero matches — so the spec does not say which wins | **Both kept; new module `gateways/identity.py` per §3's manifest.** The two cannot be unified: SPEC-003's takes `account_id` as an **input**, and A5's whole claim is that the account is the **output**. Opposite data flow, not a refactor. They also answer different questions — D16 asks *"which answers may an unlinked sender receive"* in a deployment where the account is already known (staff + empty scope is its fail-closed answer); D12 asks *"which account does this sender belong to"*, which has no safe default. `telegram_link_service.py` is live on the Telegram path and is left untouched |

**Which resolver governs which ingress — settle it here, not at G5.** After G5 there will be two
ingress paths with opposite unlinked semantics, and that is safe only while each stays on its own
side:

| Ingress | Resolver | Unlinked sender |
|---|---|---|
| `cli/telegram.py monitor` (short polling, single-tenant, account known from config) | `telegram_link_service.sender_authz` → `resolve_sender` | `staff` + **empty** scope (D16) — most restrictive available, fails closed on *"which answers"* |
| `POST /webhooks/telegram` (G5, multi-tenant, account is what's being discovered) | `gateways.identity.resolve_sender` | **raises `UnlinkedSender`** (D12/N2) — there is no safe default for *"whose estate"* |

**G5 must not call `sender_authz` at the webhook edge**, and G6's cutover is what eventually
retires the polling path's exemption. Until then the D16 branch is reachable and correct for the
transport it serves.

### [x] G2 — Step 2: sender identity — *dep: G1 — MUST precede G3*
- [x] G2.1 · §6 Step 2 · A5 · `resolve_sender` and the **one legitimate unscoped lookup** (§5.1) — which account to scope to is precisely what is being determined · verify: `tests/unit/test_identity.py::test_resolves_single_account`
- [x] G2.2 · §6 Step 2 · A6 · **an unlinked sender raises `UnlinkedSender` and is never defaulted** (D12/N2) — the whole stake. Pair the negative with a positive (§0.5b): a *linked* sender in the same test resolves · verify: `tests/unit/test_identity.py::test_unlinked_fails_closed`
- [x] G2.3 · §6 Step 2 · A7 · a sender linked in two accounts (legitimate under D5) resolves **by chat**; a DM from them is refused as ambiguous, never guessed · verify: `tests/unit/test_identity.py::test_multi_account_sender`

### [x] G3 — Step 3: the linking flow — *dep: G2*
- [x] G3.1 · §6 Step 3 · A8 · **G-refusals** — expired, replayed, wrong-gateway and cross-account codes each fail with a **distinct** message, not four paths into one generic error · verify: `tests/unit/test_linking.py::test_refusal_matrix`
- [x] G3.2 · §6 Step 3 · A9 · single-use: a second redemption is refused rather than rebinding, so a forwarded code cannot hijack an existing link · verify: `tests/unit/test_linking.py::test_single_use`
- [x] G3.3 · §6 Step 3 · A10 · revoking a membership removes the link **with no extra code** — `ondelete=CASCADE` makes D4 structural (already shipped in `0007`, §0.3) · verify: `tests/unit/test_linking.py::test_cascade_revocation`

### [x] G4 — Step 4: thread `account` through the core — *dep: G3 — MUST precede G5*
- [x] G4.1 · §6 Step 4 · A11 · **G-branches** — `dispatch_items` takes a required, never-defaulted `account`; the test walks the 15 `REVIEW_SCHEMA` categories **from the tree** and asserts each writes only within the resolved account. **The phase's definition of done** · verify: `tests/integration/test_gateway_tenancy.py::test_cross_account_isolation`
- [x] G4.2 · §6 Step 4 · A12 · `is_trusted_sender` resolves the allowlist and staff match **within** an account — without it, a sender known in B is trusted in A (D8). **New test, not the name §8 gives** (C6) · verify: `tests/integration/test_gateway_safety.py::test_trust_is_account_scoped`
- [x] G4.3 · §6 Step 4 · A13 · `property_slug` is **unchanged and orthogonal** (D13/N5) — an account holds several properties and the chat→property map still decides which house. **New test** (C6), **written at module level** despite the file nesting everything in `TestResolveDefaultProperty` (C10) · verify: `tests/unit/test_gateway_property_resolution.py::test_unchanged_under_tenancy`

**G5 deviations and measurements.**

| # | What was found | Resolution |
|---|---|---|
| **D7** | **A14 at G5 is a secret-token mismatch, not an HMAC.** §5.4 specifies both verifiers, but they are not equally strong: Telegram **signs nothing** — it echoes a caller-chosen `secret_token` in a header — while the real HMAC-over-raw-body (`X-Hub-Signature-256`) belongs to the *WhatsApp* route, which is Step 7 | Both verifiers built now (pure, and §5.4 specifies both); only `/webhooks/telegram` is wired. `verify_telegram` still **takes** `raw_body` it does not read, so the call site cannot drift into parsing-first when Step 7 shares the module. On the Telegram path N4 survives as **ordering discipline**, not as a guarantee over bytes — recorded in `ALLOWLIST_MECHANISMS` in those words, so the weaker mechanism is named where a reviewer meets it |
| **D8** | **`ProcessedIdStore` cannot run at the unscoped edge.** F7 says redelivery is already idempotent — true, but measured: the store opens its *own* `get_session()` and reads `Configuration`, a `TENANT_TABLES` model, so with no account bound both `contains()` and `add()` raise `LookupError` from the fail-closed tenancy filter. Probed directly before designing around it | Dedup runs **after** `resolve_sender`, inside the scoped session. Consequence stated rather than discovered later: **an unlinked sender's redeliveries are not deduped** and re-receive the linking prompt each time. The alternative — a transport-level store readable before any account is known — is a new unscoped write path in the one place D12 forbids one |
| **G5.4 measured** | C2 asked whether the Host/Origin exemption covers this path. SPEC-005 measured that it did **not** cover `/unsubscribe`, which needed its own entry | **It does cover it**, and the test proves it rather than reasoning it: a POST with `Host: mihomes.ai` returns 401 (reached the handler, failed verification) rather than 400 (turned away by the guard). No new exemption added. **A drift guard was added instead**: `WEBHOOK_PATH_PREFIX` lives in `webhooks.py` while this route lives in `gateways.py` — exactly the separation that module's comment warns about — so a test asserts every gateway route stays under the prefix. Renaming the path would otherwise silently re-arm the Host guard and 400 every live delivery |
| **allowlist** | The harness predicted a *"third `PERMANENT_ALLOWLIST` entry"* | It is the **fourth** — `auth`, `webhooks`, `unsubscribe` were already there. Not range-checked (C7); the gates were run and they name the count themselves |

### [x] G5 — Step 5: the webhook route — *dep: G4*
- [x] G5.1 · §6 Step 5 · A14 · raw-body verification **before any parse** (D7/N4) — extend `web/routes/webhooks.py`'s proven Stripe pattern (C2); a forged signature is rejected **with no DB write** · verify: `tests/integration/test_gateway_webhook.py::test_bad_signature_no_write`
- [x] G5.2 · §6 Step 5 · A15 · a valid update reaches `process_and_respond` under the **right** account — resolve once at ingress (D11/N3), never in the 15 category branches · verify: `tests/integration/test_gateway_webhook.py::test_routes_to_account`
- [x] G5.3 · §6 Step 5 · A16 · redelivery creates nothing twice — providers redeliver aggressively and `ProcessedIdStore` (F7) already makes this idempotent · verify: `tests/integration/test_gateway_webhook.py::test_redelivery_idempotent`
- [x] G5.4 · C9 · — · third `PERMANENT_ALLOWLIST` entry **plus** its `ALLOWLIST_MECHANISMS` reason (the signature); **probe** whether the Host/Origin exemption covers the path — SPEC-005 measured that it did *not* cover `/unsubscribe` · verify: `tests/unit/test_route_declarations.py::TestAllowlistDiscipline::test_every_allowlisted_module_names_its_mechanism`

**G6 deviation, and the defect it uncovered.**

| # | What was found | Resolution |
|---|---|---|
| **D9** | **A17's mechanism is the shared `ProcessedIdStore`, not `poll_lease`.** Step 6 and §0.6 both point at `poll_lease` (`dedup.py:145`), but it was built for poller-vs-poller: a 90-second TTL acquired and released around one polling cycle. The webhook handler is stateless and short-lived, so it could only either contend for that lease per request — racing a poller that holds it for 90s, and **dropping updates** — or ignore it, which is no interlock at all. Measured separately: **no production code calls `poll_lease` at all**; only `test_dedup.py` does | Step 6's own wording settles it — *"cannot both **process** one update"*, not "cannot both run". A17 is discharged by the shared store: whichever transport records an id first, the other skips it. `poll_lease` is left exactly as it is, still correct for the hazard it was written for. Worth carrying to G7: Telegram refuses `getUpdates` while a webhook is registered, so *that* transport has a provider-level interlock — **the WhatsApp path has none** (N6 says so), which is why the guarantee must not rest on it |
| **defect found** | **The M22 defect, reintroduced by G5 and caught by G6.** G5's route used its own store key (`gateway.telegram.processed`) while the CLI monitor used `telegram.processed_ids` — **two disjoint stores**, so an update processed by one transport was invisible to the other. That is precisely what `dedup.py`'s docstring says the module exists to prevent: *"each gateway had FOUR disjoint processed-id stores … an id handled by one poller was invisible to the other and messages were double-processed into duplicate issues/tasks"* | Both now import `PROCESSED_IDS_KEY` and `MAX_PROCESSED_IDS` from the extractor rather than spelling either out; the monitor's local `cap=2000` is gone, since two caps on one key means the shorter evicts ids the longer still considers handled. `test_both_transports_share_one_store` reads both modules' source and fails if either grows a local key again — the behavioural A17 test alone would not catch it, because a webhook that silently failed to write also "does not double-process" |

### [x] G6 — Step 6: the polling cutover — *dep: G5*
- [x] G6.1 · §6 Step 6 · A17 · **the webhook and a running poller cannot both process one update** (N6) — `poll_lease` exists because concurrent pollers already bit once; a webhook plus a live poller is that hazard wearing a new hat · verify: `tests/integration/test_gateway_webhook.py::test_no_double_transport`
- [x] G6.2 · **U3** · — · O2's cutover decision **cannot be made from the repo** — split per conventions §3.3: route, verification and handler ship and A14–A17 prove them; where the webhook terminates locally is recorded as an unmet gate · verify: §0.8 U3

**G7 deviations, and the G5 defect writing A19 uncovered.**

| # | What was found | Resolution |
|---|---|---|
| **defect found** | **G5's webhook envelope was missing `propertySlug`, and every webhook message was being dropped before dispatch.** `responder.py:208` filters `[m for m in messages if m.get("propertySlug") or property_slug]`, and the route passed neither — so `process_and_respond` returned `{"logged": 0, "errors": ["No linked chat found"]}` for every delivery. **A14/A15/A16 passed anyway**, because `WATCHED_TABLES` included `audit_log` and *sender resolution* writes audit rows: `sum(counts) > 0` was satisfied before dispatch ever ran. Three criteria met by the wrong mechanism | Envelope completed to the full **eleven keys** `telegram/client.py:164` produces; the route now resolves the chat→property map inside the scoped session (it is a `Configuration` read, so it cannot happen at normalize time). `audit_log` removed from `WATCHED_TABLES` — counting only tables a *dispatched item* creates is what makes those assertions mean what they say. The AI stub moved to `analyze_messages`, the responder's own seam; stubbing `ai_response` alone left the batch abandoned one layer up |
| **D10** | The DAG's G7.2 row names **six** envelope keys; the shape actually produced has **eleven** | A19 asserts the whole set, and `test_the_parity_target_is_the_shape_the_telegram_client_really_produces` reads `TelegramClient.normalize_update`'s source so the constant cannot drift from live code. "The same dict", not "a compatible subset" — the defect above is precisely what the weaker reading permits |
| **D11** | A18 cannot use `isinstance` **or `issubclass`** — `WhatsAppBridge` is a plain `Protocol`, and both raise `TypeError` on a non-`@runtime_checkable` one. Written the wrong way first; the suite said so immediately | Per-method conformance via `inspect.signature`, with the method list **read off the Protocol** so a fifth method fails until implemented. Non-subclassing asserted on `__mro__`, which is the direct statement of the claim. A companion test asserts the Protocol is *not* runtime-checkable, so if that ever changes this becomes a prompt to simplify rather than a permanent workaround |
| **A20 shape** | "The responder is unchanged" is vacuously true of a file nobody edited | Asserted as the **seam** instead: a `GatewayAdapter` closing over a fake Cloud client carries a real `dispatch_items` call end to end, and a static test asserts `review_common` names no concrete client at all — the coupling D2 exists to prevent, which the behavioural test alone would not catch |
| **U2 / O1** | Whether the tier supports groups is a founder call | Built tier-independent; `supports_groups` defaults to **False** (fail closed). `send_group_message` raises a named `GroupsNotSupported` **naming `whatsapp.inventory_group_jid`** rather than degrading to per-recipient sends — a silent fallback would look like the feature working while an estate's inventory reports went to one person. §7: a behaviour change the migration must state, not absorb |

### [x] G7 — Step 7: WhatsApp Cloud API — *dep: G5 — MUST precede G9*
- [x] G7.1 · §6 Step 7 · A18 · `CloudAPIClient` satisfies the **existing** `WhatsAppBridge` Protocol structurally, no subclassing (D10/F3) — it has had zero implementers since it was written, and its shape is the Cloud API's · verify: `tests/unit/test_whatsapp_cloud.py::test_protocol_conformance`
- [x] G7.2 · §6 Step 7 · A19 · an inbound Cloud API message normalizes to **the same dict** Baileys produced — the responders' contract is `jid`/`senderName`/`text`/`hasMedia`/`mediaPath`/`propertySlug` · verify: `tests/unit/test_whatsapp_cloud.py::test_envelope_parity`
- [x] G7.3 · §6 Step 7 · A20 · the responder is **unchanged** by the swap — it holds a `GatewayAdapter` (D2), never a transport · verify: `tests/unit/test_whatsapp_cloud.py::test_responder_untouched`
- [x] G7.4 · **U2** · — · O1's tier/group question is a founder cost/capability call; build the tier-independent parts. If the tier drops groups, `whatsapp.inventory_group_jid` needs a replacement routing key — **a behaviour change the migration must state, not absorb** · verify: §0.8 U2

### [x] G8 — Step 8: `notify_staff`'s fallback — *dep: none*
- [x] G8.1 · §6 Step 8 · A21 · give `notify_staff` the ladder `notify_approver` already has (F9) — on a Telegram-only install a staff member is currently **never told** their PTO was decided. **New test, not the name §8 gives** (C6), **written at module level** despite every test in the file being nested (C10) · verify: `tests/unit/test_staff_pto.py::test_notify_staff_fallback`

### [!] G9 — Step 9: retire Baileys — *dep: G7* — **see U5**
- [!] G9.1 · §6 Step 9 · A22 · **N10 forbids this until Step 7 is green *in production*, and no production exists** — `bridge/` is today's only working WhatsApp transport and deleting it early makes rollback impossible while O1 is open. **G-baileys ships as a derived gate that will pass trivially now and hold the line at cutover** · verify: `tests/unit/test_gateway_cleanup.py::test_no_baileys_imports`
- [!] G9.2 · §6 Step 9 · A23 · the watchdog supervises nothing that no longer exists (D15) — 23 references today (F6). Same U5 block: the shrink follows the deletion · verify: `tests/unit/test_gateway_cleanup.py::test_watchdog_scope`

### [x] G10 — Step 10: close the coverage gap — *dep: G9*
- [x] G10.1 · §6 Step 10 · A24 · **G-coverage** — narrow `omit` so `identity.py`, `linking.py`, `webhook.py` and both adapters are measured; the list is **derived**, and `cloud_client.py` stays omitted on network-bound grounds (U8) · verify: `tests/unit/test_gateway_cleanup.py::test_coverage_not_omitted`

### [x] G11 — the exit criterion — *dep: all*
- [x] G11.1 · §6 exit · A25 · **end to end** — a linked sender in A, by webhook, no poller running, through the Cloud API: a row in A and **nothing in B**. Pair the negative with the positive (§0.5b) · verify: `tests/integration/test_gateway_e2e.py::test_exit_criterion`

### [x] G-Final — Compound-stop verification (conventions §4.1)
- [x] F.1 · full-suite `py -m pytest -q` green (condition C) — baseline **2405 passed**; a new skip is red
- [x] F.2 · every §8 criterion green by its own named test (condition E) — **all 25, run by node id**
- [x] F.3a · walk §6 top-to-bottom: every step has a task (condition B, steps) — **10 steps + 2 prerequisites**
- [x] F.3b · `py scripts/spec006_reconcile.py --collect` exits 0 (condition B, criteria) — **derived, never range-checked (C7)**
- [x] F.4 · smoke green (condition D)
- [x] F.5 · write end-of-run report `tasks/build-loop-spec006-report.md`

---

## 2. Group-specific gates (conventions §2)

| Group | Gate | Failure class it targets |
|---|---|---|
| **G1** | migration round-trip + metadata-drift + three pinned counts + the `EXPECTED_NON_LEADING` reason | reversibility, silent table addition, an index exemption bought without argument |
| **G2** | **A6 pairs its negative with a positive** | "never defaulted" is vacuously true if nothing resolves at all |
| **G4** | **G-branches derived from `REVIEW_SCHEMA`** | the fifteenth category leaking while a hand-listed subset passes forever |
| **G3** | **G-refusals asserts four distinct messages** | four refusal paths collapsing into one generic error |
| **G5** | raw bytes before parse; probe the Host guard rather than assuming | a framework that re-serializes makes the signature pass *after* acting on unverified input |
| **G9** | `[!]` — U5 blocks honest discharge | deleting the only working transport while its replacement is unproven |
| **every** | `py scripts/spec006_reconcile.py --collect` after each group commit | the DAG drifting from §8 — and the node ids that were **already wrong** at authoring (C6) |

**Mutation-check every security-, money- and privacy-relevant arm**: break it, confirm red for its
own reason, restore. **SPEC-006's tenancy arms are the highest-stakes in the set** — a surviving
mutation on A11 means a cross-tenant write nobody is watching.

**A surviving mutation has three diagnoses**, and "add a test" is right for one: redundant
condition (delete it), untested arm (add the test), inert difference (document with the
measurement).

**Re-run earlier groups' mutations after any group that moves a call site** — SPEC-005 BD5: G4
moved the provider call and a ticked criterion went vacuous. G4 here moves `dispatch_items`'
signature, which every responder calls.

## 2.1 RUN STATE — where a resuming session picks up

**AUTHORED, NOT RUN.** Pre-flight complete (§0.6, eleven findings re-measured — nine hold, six
corrected), `scripts/spec006_reconcile.py` written, its parsers verified against the real spec
(25 criteria, 13 manifest entries, every label resolving to a directory), and **the gate itself
mutation-checked five ways** (§2.2 BD5) — a reconciler nobody has seen fail is decoration.

`py scripts/spec006_reconcile.py --collect` exits 0: **25/25 criteria gated**, 1/25 node ids
resolving (only A2 exists — nothing is built yet). `tests/unit/test_spec006_reconciler.py` is
green, 6 passed. **No SPEC-006 source code has been written.**

**Start at G0.** Do not start at G1: G0 exists because the prerequisite's own verification step
(A2) names a node id that does not resolve, and that must be corrected before it is trusted.

**Three things a resuming session must not re-litigate:**

1. **N9 does not fire** (§0.3) — `0007_telegram_links.py` ships the table correctly. Step 1 adds
   only `gateway_link_tokens`.
2. **P6/U1 stays open** (§0.2) — building on this branch is justified by measurement, and is *not*
   the branch reconciliation. Do not record SPEC-006 as closing it.
3. **G9 is `[!]` by N10** (§0.8 U5) — the derived gates ship; the deletion waits for a production
   the project does not have.

## 2.2 DEVIATIONS from the spec, with the measurement that forced each

> **Labelled `BDn` — build deviation — not `Dn`.** The spec has its own `D1`–`D15` (design
> decisions); numbering deviations in the same namespace collided twice during SPEC-005.

**BD1 — F1 is false, and it is the largest correction in the set.** The spec's §1.5 opens with
*"Zero tenancy anywhere in `src/` … not merely in gateways but in the entire package"*, and every
§5 signature is explicitly *"written against SPEC-002/003's design"*. Measured: **91 files carry
`account_id`**, `can()` and `require_permission` are live, `telegram_links` is registered and
migrated. The spec's forward references now describe **code**, not design. Recorded because a
builder who trusts F1 re-invents a tenancy layer that already exists — the same failure §0.1 warns
about, arriving through staleness rather than through the wrong branch.

**BD2 — three of the four "existing file, extended" node ids do not resolve** (§0.6 C6). A2's is
the load-bearing one: it is the prerequisite's *own* verification step, so P2 would have been
gated on a name `pytest` answers "not found" to. Found by running `--collect-only` before writing
the harness, which is SPEC-005 BD2's lesson applied one phase earlier than it was learned.

**BD3 — the §4.1 model uses `String(36)` PKs; this repo uses `PGUUID(as_uuid=True)`.** Every
SPEC-003+ model, `telegram_link.py` included, uses the latter, and `test_baseline_matches_metadata`
compares metadata against the migrated schema. Following the spec literally would fail that gate.
Follow the repo.

**BD4 — A22/A23 ship as `[!]`, not `[x]`.** N10 conditions Step 9 on Step 7 being *"green in
production"*; there is no production Cloud API deployment and O1 is unresolved. The derived gates
(G-baileys) are built so the line holds at cutover, but discharging them now would be recording a
green that means nothing. Split per conventions §3.3, the same shape as SPEC-005 G5.3's U10.

**BD5 — the reconciler carries two exemption tables, and both are mutation-gated.** Correcting
§8's wrong node ids (BD2) means the script must permit a *documented* disagreement, and permitting
disagreement is the one thing a reconciler must not do casually. Both tables therefore have an
expiry test (`tests/unit/test_spec006_reconciler.py`), and both were mutation-checked before being
believed:

| # | Mutation | Result |
|---|---|---|
| M1 | ungate A11 — the definition of done | ✅ RED: *"declared in §8 but NO DAG task gates it"* |
| M2 | point A11's gate at a plausible-but-wrong test name | ✅ RED, naming both what the DAG says and what §8 declares |
| M3 | point A2 at a name **neither** §8 nor the correction allows | ✅ RED — the table permits exactly one verified name, not any name |
| M4 | remove a `PENDING_TESTS_IN_EXISTING_FILES` entry | ✅ RED: its unresolved node id surfaces immediately |
| M5 | add an entry whose test **already resolves** (a stale exemption) | ✅ RED: *"now RESOLVES, so the group has landed. Remove it — leaving it exempts a real node id from the collect check forever."* |

M5 is the one worth keeping in mind: it is the failure mode where a run finishes green having
stopped checking. **G-Final's F.3b must run with `PENDING_TESTS_IN_EXISTING_FILES` empty.**

**BD6 — `--collect` found a wrong node id in this harness, written by me.** G1.3 named
`test_matrix.py::test_every_model_is_classified`; the test is nested in `TestEntityClassification`.
Precisely the defect the pre-flight had just caught four times in the spec, reproduced in the
harness written to catch it — and caught only because `--collect` executes rather than reads.

**BD8 — the expiry test has a blind spot, and it is documented rather than patched.** A pending
test written under a *nested* name does not resolve, so
`test_a_pending_test_that_now_exists_must_be_removed` stays green while the entry silently becomes
permanent (C10). Two of the three pending files nest everything, so this was likely rather than
theoretical.

Fixed by instruction — "write these at module level" in the DAG rows, the pending set and §0.6 —
rather than by teaching the test to guess at nested variants. Guessing would mean the test
asserting things about names nobody declared, and the real invariant is simply *§8's node id is
what `--collect` enforces*. Recorded because the blind spot still exists for any future entry, and
a reader deserves to meet it here rather than discover it.

**BD7 — F11's six test files are real, but two are named for their subject.** A first sweep for
`test_gateway*` found four and suggested the spec had over-counted. `test_telegram_client.py` and
`test_whatsapp_drain.py` are the other two. Recorded so the next session does not re-derive a
false discrepancy — and as a reminder that a glob is a hypothesis, not a census.
