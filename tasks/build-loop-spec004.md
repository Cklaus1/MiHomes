# SPEC-004 Build Loop — Phase 3: Billing / Freemium

> **Input spec:** `docs/specs/SPEC-004-phase3-billing-freemium.md` (852 lines, *Ready to build*,
> **one open decision — O1**, blocks-ship only)
> **Conventions:** `tasks/build-loop-conventions.md` — stop condition, poison ceiling, circuit
> breaker, artifact routing are defined there and inherited here **unchanged**.
> **Branch:** `worktree-spec-build-harness`. **Target ref:** HEAD `4178286` (SPEC-003 complete).
> **Invocation:** `/loop tasks/build-loop-spec004.md`

**Phase 1 defended the boundary between customers. Phase 2 defended the boundary inside one.
Phase 3 defends the boundary between what was paid for and what was not** — and it is the first
phase where a defect costs money in *both* directions. The spec's own framing (§ "The stake"):

> A gate that fails open gives away the product. A meter that fails open hands an unbounded Claude
> bill to whoever is paying for inference. **Both failure modes look exactly like the feature
> working.**

**A11 is the definition of done** (spec §8): *"The AI meter binds at every entry point, or it
bounds nothing."* A meter on the web route but not the Telegram bot, the CLI, or `agent_stream`
does not cap spend. If A11 is not green, Phase 3 has not shipped regardless of what else is.

**A31 is the exit criterion** (`SAAS_PRD:180`): a Free gate flips to Pro **via webhook, not
redirect**. Phase 3 is the **MVP cut line** — the last phase before the product can be sold.

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, all four DB env vars set | ✅ PostgreSQL 18.x, `mihomes_test` + `mihomes_phase0` exist |
| P2 | `MIHOMES_SECRET_KEY` set (**new since SPEC-003 U1** — a required env var, not optional) | ✅ set; without it the config service refuses secret writes |
| P3 | SPEC-002 landed: `accounts` Stripe columns, RLS, `TenantOwned`, scoped session | ✅ verified §0.6 — **all six Stripe columns pre-ship**, so N13 holds |
| P4 | SPEC-003 landed: `can()`/`usage()`, `require_permission`, the matrix, `PLAN_LIMITS_PHASE3` | ✅ verified §0.6 — and it pre-shipped more than the spec expects (**C4**) |
| P5 | SPEC-001 `services/email/` package (Step 15 needs it) | ✅ exists — `provider.py`, `service.py`, `__init__.py` |
| P6 | A Stripe account with test-mode keys | ❌ **NOT PRESENT — see §0.9.** Does *not* halt: `FakeBillingProvider` covers every criterion but the live-key ones |

**Environment — pass inline, the worktree guard rejects `export` chains:**

```
DATABASE_URL               postgresql+psycopg://postgres@localhost:5432/mihomes_test
MIGRATION_DATABASE_URL     postgresql+psycopg://postgres@localhost:5432/mihomes_test
TEST_DATABASE_URL          postgresql+psycopg://postgres@localhost:5432/mihomes_test
LANDING_TEST_DATABASE_URL  postgresql+psycopg://postgres@localhost:5432/mihomes_phase0
MIHOMES_SECRET_KEY         <Fernet key — `mihomes config generate-key`>
```

Invoke pytest as `py -m pytest`, never `python` (Store shim). Use `--color=no` when parsing
output programmatically — ANSI codes defeated a mutation harness during SPEC-003 (lessons).

---

## 0.3 Stop condition

Per conventions §0, all five. Conventions §0.1: *"SPEC-003 onward — C is suite green **including
this spec's new tests**."*

| | Condition | For SPEC-004 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 below |
| **B** | every §6 step tasked **and** every §8 criterion gated | F.3a + F.3b |
| **C** | full suite green **including this spec's new tests** | baseline below → final |
| **D** | smoke green | `tests/integration/test_smoke_all_tools.py` — **a Step 10 target**, see §0.5 |
| **E** | every §8 criterion green by its own named test | all 31, F.2 |

**Measured baseline — HEAD `4178286`, correct env, before any SPEC-004 code:**

```
py -m pytest -q   →   1945 passed, 3 skipped, 2 xfailed, 0 failed   (266s)
```

The 3 skips and 2 xfails are inherited and known-benign. Conventions §0 makes **a new skip red** —
a skipped test is the most likely way this harness reports a false success.

### 0.4 Condition E has a hole this spec cannot close by itself — one gate closes it

Conventions §0: *"a stub can satisfy A+B+C+D"*. SPEC-004 already anticipated its own version of
this and wrote the fix into the criterion (§8, A11):

> A11 must discover entry points **from the tree** and assert each one increments the counter. A
> hand-maintained list in a test file rots the first time someone adds a dispatch path, and it
> rots silently.

This is the same principle as SPEC-003's G-census and `UNFILTERED_CLASSES`: **derive the gate from
the code, never from a transcription of it.** One derived gate is mandatory here:

| Gate | Check | Closes |
|---|---|---|
| **G-dispatch** | Every module that calls `provider.complete` / `structured_output` / `stream` — enumerated by walking `src/` at test time — produces an `AIUsageEvent`. A new dispatch path fails the suite until metered. | A11's list-rot |

Paired with **A10**'s static assertion that no module outside `services/ai/*_provider.py`
constructs an SDK client directly — the check `agent.py:78` would have failed today (**F8**,
confirmed at §0.6).

### 0.5 D's smoke file is a Step 10 target

`tests/integration/test_smoke_all_tools.py` invokes the AI tool executors against a stubbed
provider. Step 10 makes `get_provider()` return a `MeteredProvider` wrapper. The smoke file is a
call site of the thing being wrapped. Expect it to move when G10 lands; **do not soften
`MeteredProvider`'s proxying to keep it green** — the proxy requirement (F8) is the whole point.

---

## 0.6 PRE-FLIGHT RE-VERIFICATION (conventions §3.1) — measured at HEAD `4178286`, 2026-08-24

**SPEC-004 was written 2026-08-04, and its §0.1 is now false in the build's favour.** It opens:
*"Phases 0, 1 and 2 are all unbuilt… Zero hits in `src/` for `stripe`, `BillingProvider`,
`EmailProvider`, `resend`, `entitlement`, `max_homes`, `def can(`. No `User`, `Account` or
`Tenant` model. **No auth layer at all.**"*

All three phases have since landed. **This section is the re-verification §0.1 itself demands**
(*"Re-verify §4 and §5 against the tree before building"*). **Corrected values are authoritative
over the spec's prose.**

### Claims that hold — verified, not assumed

| Claim | Source | Measured |
|---|---|---|
| `agent.py` constructs its own Anthropic client, bypassing the factory | F8 | ✅ **`agent.py:78`** — `anthropic.Anthropic(api_key=api_key)`. The only bypass; the other three constructions are inside `*_provider.py` where they belong |
| `provider.stream()` is called but **not declared** on the `AIProvider` Protocol | F8 | ✅ called at `agent.py:44`; Protocol declares `complete` + `structured_output` only |
| `ai_conversations.tokens_used` is dead — no assignment anywhere | F9 | ✅ zero `tokens_used=` writes in `src/`. Only the column definition, an unrelated dataclass field, and archive's raw-SQL column list |
| `archive.py` DELETEs `ai_conversations` rows | F10 | ✅ `archive.py:191-199` selects-then-archives-then-deletes. **D18 (materialized counters) stands** |
| The cached `_get_provider` in `calendar_sync`/`staff_pto`/`routes/calendar` is **Google Calendar**, unrelated | F10 | ✅ confirmed — 4 false positives, named here so a later reader does not re-derive it |
| `accounts` pre-ships every Stripe column | §3, N13 | ✅ all six: `stripe_customer_id`, `stripe_subscription_id`, `subscription_status`, `current_period_end`, `trial_ends_at`, `trial_used_at`. **No `accounts` migration — N13 holds** |
| `services/email/` exists | P5 | ✅ SPEC-001 shipped it |
| `stripe` is not a dependency | §3 | ✅ absent from `pyproject.toml` — Step 1 adds it |

### Claims corrected — build against the right-hand column

| # | Claim | Spec | **Measured at HEAD** |
|---|---|---|---|
| **C1** | "Phases 0, 1, 2 unbuilt; no auth layer" | §0.1 | **All three landed.** 1945 tests green. Every forward reference in §5 now describes *code*, not a spec |
| **C2** | `config.py:14` hardcodes `DB_URL = sqlite:///…` | §0.1 | **False** — SPEC-002 D1 dropped SQLite; Postgres throughout |
| **C3** | "`entitlement` — zero hits in `src/`" | §0.1 | **`entitlements/` is a full package** — `limits.py`, `service.py` |
| **C4** | Step 8 is "real limits… replacing D7's free-unlimited" | §6 Step 8 | **SPEC-003 pre-shipped `PLAN_LIMITS_PHASE3` with §3.1's numbers verbatim, inert**, plus `test_tables_declare_identical_keys` so the two cannot drift. **Step 8 is a one-line swap of which table is active**, exactly as `limits.py`'s docstring promises. See **C11** for the O1 tension this creates |
| **C5** | `usage()` "stops being a stub" | §5.3 | Signature already final: `usage(account, meter) -> UsageReport`, returning `limit=None` deliberately (*"`None` cannot be mistaken for a measurement"*). Only the **body** changes |
| **C6** | §4's DDL: `String(36)` PKs with `default=new_id` | §4.1, §4.2 | **Superseded.** The tree is `Mapped[uuid.UUID]` + `PGUUID(as_uuid=True)` since SPEC-002. **Transcribing §4 verbatim would fail `test_baseline_matches_metadata`.** Conventions §6's *"build the spec, not your idea of the spec"* yields here to *"divergence compounds — re-verify §4"*, which §0.1 itself instructs |
| **C7** | §9: "migrations are never exercised by any existing test — the only conftest has two fixtures" | §9 | **Stale.** `tests/conftest.py` has ~14 fixtures; `tests/integration/test_pg_baseline.py` already gates round-trip (`test_upgrade_then_downgrade_is_clean`), metadata drift (`test_baseline_matches_metadata`) and single-head. **A30 is nearly free** — it inherits these |
| **C8** | `billing.manage` needs a new matrix key | D8 implication | **Already exists** — `actions.py:151`, row 15, `(owner=ALLOW, admin=DENY, staff=DENY)`, `Access.ACCOUNT`. **No matrix change for D8**, and none is possible without breaking A1 (see C9) |

### C9 — SPEC-003's fail-closed gates will reject SPEC-004's new code

**This is the finding that shapes the DAG, and it appears nowhere in SPEC-004** — the spec
predates every gate below. Each is a *live test that turns red* the moment SPEC-004's code lands,
unless the step accounts for it. Not defects: they are the phase working as designed.

| # | Gate | What it does to SPEC-004 | Which step must handle it |
|---|---|---|---|
| **C9a** | `test_matrix.py::test_every_model_is_classified` — fail-closed over every mapped model | SPEC-004 adds **three** models. All three need an `ENTITY_CLASSES` entry or the suite goes red | **G3** (ledger), **G10** (usage tables) |
| **C9b** | `test_route_declarations.py` — `CEILING == 0`, `UNDECLARED_MODULES == set()` | `POST /webhooks/stripe` has **no session, no principal, no tenant scope by design** (N3, `BILLING:414`). It cannot declare a matrix action. Adding it to `UNDECLARED_MODULES` breaks `test_ceiling_is_not_slack`, which pins the two together | **G4** — see the decision below |
| **C9c** | `tenancy/registry.py::check_registry` — every table classified as tenant or global, explicitly | Three new tables need registry entries. The ledger is `GLOBAL_TABLES` (same carve-out as `sessions`, which the module docstring already justifies) | **G3**, **G10** |
| **C9d** | `test_pg_baseline.py::test_baseline_matches_metadata` | Caught a `DateTime(timezone=True)`-vs-naive mismatch in migration 0009 during SPEC-003. **C6's UUID correction is the same class of trap** | **G3**, **G10** |
| **C9e** | `test_composite_indexes_lead_with_account_id` | §4.1 declares `index=True` on the ledger's `account_id`. The ledger is **not** a tenant table so the rule may not bind — **measure before assuming**. This exact rule bit `staff.user_id` during SPEC-003 | **G3** |
| **C9f** | `authz/query_scope.py::UNFILTERED_CLASSES` — every entity class is filtered or *declared* unfiltered, with a reason | Whatever class the three new models take, the declaration must be honest. `AIUsageEvent`/`AIUsageRollup` are account-level billing data staff must never read | **G3**, **G10** |

**C9b's decision, made here so G4 does not improvise it.** The webhook route is unauthenticated
*by design and verified by a different mechanism* — a signature over raw bytes (N3). That is not
the same as "not yet declared", which is what `UNDECLARED_MODULES` means, and it is not the same
as `PERMANENT_ALLOWLIST`'s existing entry either (`auth` is excused because *identity does not yet
exist*; here identity is irrelevant — Stripe is not a user).

> **Add it to `PERMANENT_ALLOWLIST` with its own reason string**, which is what that list is for:
> *"Each entry carries a one-line justification, because an unauthenticated route is a decision."*
> The temporary list stays empty and the ceiling stays 0.

**And add a derived gate, because the permanent list is now load-bearing in a way it was not**:
every `PERMANENT_ALLOWLIST` module must have *some* non-session authentication mechanism.
`test_permanent_allowlist_entries_are_still_live` already catches stale entries; it does not catch
a module added to the list to silence the harness. Same shape as `UNFILTERED_CLASSES` — the
non-decision is declared as data, and a test asserts the declaration is complete.

### C10 — the AI dispatch census, pinned now (A11's ground truth)

A11 must derive this at test time. Pinned here so a divergence between the derived set and the
measured set is visible rather than silent — the SPEC-003 pattern for the 15 tool executors.

**9 `get_provider()` call sites** (excluding the 4 Google-Calendar `_get_provider` false
positives, and the definition itself):

```
services/ai/agent.py:41          services/ai/assessors.py:129,181,222
services/ai/orchestrator.py:65,239   services/ai/reports.py:171,398
services/gateways/review_common.py:533   services/resume_ranker.py:138
services/weather_tasks.py:78
```

**14 dispatch sites across 8 modules:**

```
agent.py:44 (stream)            assessors.py:145,204,231 (structured_output)
orchestrator.py:91,251 (complete)   reports.py:173,400 (complete)
review_common.py:566,575,585 (structured_output)   resume_ranker.py:169 (structured_output)
weather_tasks.py:102 (complete)     web/routes/ai.py:518 (via provider_stream)
```

**Plus the one bypass: `agent.py:78`** — `anthropic.Anthropic(api_key=api_key)`, reached through
`provider_stream` from `web/routes/ai.py:518`. **This is why Step 9 precedes Step 10.**

### C11 — O1 vs. the literals already in the tree, decided here

SPEC-004 §1.3 says every step *"targets config keys and `STRIPE_PRICE_*` env vars, **never
literals**, so the code is complete and testable before the numbers exist."* But SPEC-003 already
shipped `PLAN_LIMITS_PHASE3` containing §3.1's placeholder numbers **as literals** (C4). The two
statements conflict.

> **Decision: the two halves are different things, and both are right.** `STRIPE_PRICE_*` must be
> env vars — a price id is deployment identity and a wrong one is a real charge (D3, §9). The
> *limits* (`max_homes: 1`, `ai_calls_per_month: 200`) are product definition, already committed,
> already gated by `test_tables_declare_identical_keys`, and moving them to env would make the
> plan table unreadable and untestable for no safety gain. **Limits stay literal-with-a-swap;
> prices are env-only.** O1 changes the numbers in one file, not the mechanism.

Recorded because a later reader will otherwise "fix" one half to match the other.

### C12 — bug found during pre-flight → `opportunities.md`

None found at pre-flight beyond the corrections above. Any surfaced mid-run follow conventions §5.

---

## 0.7 O1 — the one open decision, and why it does not block the run

Conventions §3.3: classify each `O` as **blocks-build** or **blocks-ship**. **O1 (the actual
prices and limits) is blocks-ship, and the spec says so itself** (§1.3): *"**Launch configuration
only, not the build.**"*

Every step targets config keys and env vars; the numbers arrive when the founder sets them. The
gate is creating the Stripe Products and setting `STRIPE_PRICE_*`. **Do not route this to the user
mid-run** — it is recorded in §0.8 and the end-of-run report as an unmet launch gate.

## 0.8 UNMET LAUNCH GATES — carried forward, not silently satisfied

Conventions §3.3. Inherited from SPEC-003's report plus this phase's own.

| # | What is off / unresolved | Owner |
|---|---|---|
| **U1** | **O1 — the ~20 placeholder prices and limits.** Mechanism ships; the numbers do not. A perfectly correct billing system charging the wrong amount is still wrong, and no test in §9 will say so | founder |
| **U2** | **No Stripe account / test keys present** (P6). Every criterion is provable against `FakeBillingProvider`; **nothing here proves the live Stripe account's own configuration** — Products, prices, tax, the endpoint secret, whether the restricted key is actually scoped right. A mis-scoped key fails at runtime, in production (spec §10) | founder |
| **U3** | **Revenue correctness.** §8 proves the mechanism, never the amounts (spec §10) | founder |
| **U4** | **Cost attribution below the account.** The meter counts calls per account — that is what `PRICING` §5.1 bills. One member can exhaust the quota with no visibility into who (spec §10) | accepted |
| **U5** | **Inference cost vs. price.** `ai_calls_per_month` caps *calls*, not tokens. The event log records `tokens_in`/`tokens_out` so it becomes measurable; nothing acts on it until metered billing (Phase 4+) | Phase 4+ |
| **U6** | **Inherited from SPEC-003 §10, unchanged:** mis-declared action keys (U2 there), aggregate inference (U3 there), the Telegram bot's transport (U4 there) | as recorded |
| **U7** | **SPEC-003's O1 is CLOSED** — secrets are Fernet-encrypted at rest as of SPEC-003 U1. **SPEC-004 §10's first bullet and N12 are stale**: they say provider keys "remain plaintext" and `mihomes config list` "still prints them unredacted". Both were fixed. N12's *rule* still holds (`STRIPE_SECRET_KEY` from env, never `configurations.value`) but its *reason* has changed | ✅ resolved |

---

## 1. Task DAG

Conventions §1.3: **one step per group by default**; the group commit is the resume point. Format
is conventions §4: `checkbox + ID · spec-ref · criteria · imperative · verify:`.

**Three ordering constraints are load-bearing** (spec §6): **G9 before G10** (close the bypass
before metering, or the suite goes green with an uncapped bill), **G12 before G13** (the scheduler
exists before the trial needs it), **G3 before G4** (the ledger exists before the webhook writes).

### [x] G1+G2 — Steps 1–2: billing provider seam + price map — *dep: none* — *21 tests; 1945 → 1966; 2 arms mutation-verified; commit `d4ae614`*

> **Landed as one commit, and the reason is the import graph.** `stripe_provider.create_checkout_session`
> resolves `(plan, interval)` through `prices.py`, so G1 cannot be green without G2's module — and
> stubbing it would make the D3 assertion vacuous, which is the one thing Step 2 exists for. The
> group boundary bends to the dependency rather than the dependency being faked to preserve it.

- [x] G1.1 · §6 Step 1 · — · `services/billing/provider.py` reusing `BILLING` §4.1's `BillingProviderError`/`BillingAuthError`/`WebhookVerificationError`, `SubscriptionState`, `NormalizedEvent`, `BillingProvider` Protocol **verbatim** (N5: the adapter never touches the DB) · verify: `tests/unit/test_billing_provider.py::test_fake_satisfies_protocol_structurally` ✓
- [x] G1.2 · §6 Step 1 · — · `get_billing_provider()` mirroring `ai/provider.py`'s string dispatch + lazy import + explicit `else: raise`; **takes no api_key** (§9: env only) · verify: `tests/unit/test_billing_provider.py::test_unknown_provider_names_supported_list` ✓
- [x] G1.3 · §6 Step 1 · — · `stripe_provider.py` + `stripe>=11.0` in `pyproject.toml` + coverage `omit` (§9 precedent) · verify: `tests/unit/test_billing_provider.py::test_factory_returns_stripe` ✓
- [x] G2.1 · §6 Step 2 · A29 · `prices.py` resolving `(plan, interval) -> price_id` from `STRIPE_PRICE_*` env, following `ai_config.py`'s env→raise precedent · verify: `tests/unit/test_prices.py::test_missing_env_names_the_var` ✓
- [x] G2.2 · §6 Step 2 · A29 · **D3/N2** — no price id in any signature, none accepted from a request · verify: `tests/unit/test_prices.py::test_no_price_id_in_interface` ✓

> **Mutation-verified, two arms.** Adding `price_id: str` to the Protocol turns *two* unrelated
> tests red (`test_no_price_id_in_interface` and `test_fake_satisfies_protocol_structurally`) —
> which is what a real invariant looks like from outside. And `plan_for_price_id` returning
> `"free"` instead of `None` for an unknown id fails `test_unknown_price_id_is_none_not_free`:
> **an unrecognised price id must never default to Free**, because it means a customer bought
> something this deployment cannot name, and recording that as Free strips entitlements they are
> being charged for.

### [x] G3 — Step 3: the idempotency ledger + migration — *dep: G1 — MUST precede G4* — *19 tests; 1966 → 1985; 2 arms mutation-verified; commit `b672571`*
- [x] G3.1 · §6 Step 3 · — · `models/processed_webhook_event.py` per §4.1, **corrected to UUID PKs (C6)**; `UniqueConstraint(provider, provider_event_id)` — the dedup signal itself, not a bare index · verify: `tests/unit/test_webhook_tenancy.py::test_unique_constraint_is_the_dedup_mechanism` ✓
- [x] G3.2 · §6 Step 3 · A6 · migration `0010`; **ledger is `GLOBAL_TABLES`, no RLS** (B7, C9c) — same carve-out as `sessions` · verify: `tests/unit/test_webhook_tenancy.py::test_ledger_not_rls` ✓ **plus** `test_no_later_migration_adds_a_policy`, sweeping every revision file — A6 as written names one migration, and the danger is a *later* one
- [x] G3.3 · C9a,C9f · — · classify `ProcessedWebhookEvent` as `EntityClass.GLOBAL` · verify: `tests/unit/test_matrix.py::test_every_model_is_classified` ✓
- [x] G3.4 · C9d,C9e · A30 · migration round-trip + metadata-drift gates (inherited, C7); table count 47 → 48 · verify: `tests/integration/test_pg_baseline.py::test_baseline_matches_metadata` ✓

> **C9 fired four times, which is the phase working.** Adding one model turned four SPEC-003 gates
> red before a line of billing logic existed. The fourth was a genuine design conflict, not a
> missing entry: the registry forbids `account_id` on a global table, and §4.1 puts one on the
> ledger deliberately.
>
> **The distinction is input versus output.** The rule exists because such a column invites RLS,
> and RLS on a table read before account context exists returns zero rows (D3) — which binds when
> the column is an *input* to visibility. The ledger's is an **output**: the account is discovered
> by resolving a Stripe customer id (D2) and then recorded, never consulted to decide who may read
> the row, and legitimately NULL.
>
> **The general rule was not relaxed** — A6 covers one table, and says nothing about a fourth
> global table added later, which is what the rule is prophylactic against. Carve-out declared as
> data with a reason (`GLOBAL_TABLES_WITH_ACCOUNT_ID`) plus a derived liveness test. Same
> construction as U6, same reason: a correct exemption and a forgotten one are byte-identical.
> Mutation-verified both directions.
>
> **Two latent bugs found in existing tests.** `test_single_head_and_no_legacy_revisions`
> distinguished legacy revisions by `startswith("000")` — correct for 0001–0009 and *guaranteed*
> to fail at 0010; now matches the shape `\d{4}_\w+`. And this group's own A6 test failed on the
> migration's comment warning readers not to add a policy: a source scan cannot tell a warning
> from a call, so it now strips comments and the docstring via `ast` rather than the wording being
> softened — a test that punishes an explanation trains the next author to delete it.

### [x] G4 — Step 4: the webhook route — *dep: G3* — *10 tests; 1985 → 1995; 2 arms mutation-verified; commit `682f0e7`*
- [x] G4.1 · §6 Step 4 · A4 · `POST /webhooks/stripe` — raw body read, signature verified **before any parse** (N3); no session auth, no tenant scoping · verify: `tests/integration/test_webhooks.py::test_bad_signature_no_write` ✓ — asserts the **ledger count** on both sides, not just the 400: recording-then-rejecting would consume the event id and deduplicate the legitimate delivery away
- [x] G4.2 · C9b · — · module added to `PERMANENT_ALLOWLIST` with its reason; ceiling stays 0, temporary list stays empty · verify: `tests/unit/test_route_declarations.py::test_ceiling_is_not_slack` ✓
- [x] G4.3 · C9b · — · **new derived gate**: every `PERMANENT_ALLOWLIST` module declares its non-session mechanism · verify: `tests/unit/test_route_declarations.py::test_every_allowlisted_module_names_its_mechanism` ✓
- [x] G4.4 · **found here** · — · **the Host guard rejected every live webhook.** H30 400s any non-loopback `Host`; Stripe posts to the endpoint's public hostname, and no test would ever have caught it because the test client's base URL is `localhost`. Webhook prefix exempted from both guards · verify: `tests/integration/test_webhooks.py::test_host_guard_does_not_block_a_public_hostname` ✓

> **The exemption does not weaken either defence, and the reason is precise.** CSRF is *the
> browser attaching the user's cookies to a forged request*; this route reads no cookie and
> trusts no caller identity. Its authentication is an HMAC over the raw body — strictly stronger
> than an Origin header, which is advisory and unauthenticated. DNS rebinding likewise targets a
> session that does not exist here.
>
> **Mutation-verified with the blast radius, not the feature.** Widening the exemption to every
> path turns four tests red, including `test_other_routes_still_reject_a_bad_host` — a guard on
> the exemption, because a prefix typo would disable H30 app-wide while every webhook test still
> passed. Second arm: a stale `ALLOWLIST_MECHANISMS` entry fails its gate.
>
> **A real SDK behaviour the mocks would have hidden.** `StripeObject` supports `obj["key"]` and
> deliberately **raises `AttributeError` on `.get()`**. Four tests failed the first time the real
> SDK parsed a real signed payload — the case for signing real bytes rather than stubbing
> verification, since a stub returns whatever shape the test author imagined.

### [ ] G5 — Step 5: idempotency + out-of-order handling — *dep: G4*
- [ ] G5.1 · §6 Step 5 · A5 · **insert-first**, unique violation *is* the dedup signal (N4 — check-then-insert races) · verify: `tests/integration/test_webhooks.py::test_idempotent_replay`
- [ ] G5.2 · §6 Step 5 · A27 · two concurrent deliveries of one event apply once · verify: `tests/integration/test_webhooks.py::test_concurrent_delivery`
- [ ] G5.3 · §6 Step 5 · A7 · drop events whose `occurred_at` predates applied state · verify: `tests/integration/test_webhooks.py::test_out_of_order_dropped`

### [ ] G6 — Step 6: checkout + portal — *dep: G2, G5*
- [ ] G6.1 · §6 Step 6 · A28 · `web/routes/billing.py`, owner-only via the **existing** `billing.manage` row-15 key (C8) · verify: `tests/integration/test_billing_routes.py::test_owner_only`
- [ ] G6.2 · §6 Step 6 · — · `start_checkout` reuses `stripe_customer_id` rather than creating a second Customer · verify: `tests/integration/test_billing_routes.py::test_customer_reused`

### [ ] G7 — Step 7: status → entitlement mapping — *dep: G5*
- [ ] G7.1 · §6 Step 7 · A2 · `apply_subscription_state` — the **single** writer of `plan`/`subscription_status`/`current_period_end` (SPEC-002 §4.2), called by both webhook and reconcile · verify: `tests/unit/test_billing_mapping.py::test_status_table`
- [ ] G7.2 · §6 Step 7 · A8 · `past_due` keeps full access; `unpaid` restricts (D10) · verify: `tests/integration/test_downgrade.py::test_grace_then_restrict`

### [ ] G8 — Step 8: real limits — *dep: G7* — **the exit criterion's first half**
- [ ] G8.1 · §6 Step 8 · A1 · **swap the active table to `PLAN_LIMITS_PHASE3`** (C4 — a one-line change, not a rewrite) · verify: `tests/unit/test_limits.py::test_free_gates`
- [ ] G8.2 · §6 Step 8 · A3 · every `Denied` names an upgrade target (rule 4) · verify: `tests/unit/test_limits.py::test_denied_names_target`

### [ ] G9 — Step 9: close the `agent_stream` bypass — *dep: none — MUST precede G10*
- [ ] G9.1 · §6 Step 9 · A10 · route `agent.py:78` through `get_provider()`; declare `stream` on the `AIProvider` Protocol (F8) · verify: `tests/unit/test_ai_metering.py::test_no_factory_bypass`
- [ ] G9.2 · §6 Step 9 · — · the agentic tool-loop and streaming still work end to end · verify: `tests/integration/test_web_smoke.py::test_ai_stream_persists_conversation`

### [ ] G10 — Step 10: the meter — **A11, the phase's definition of done** — *dep: G8, G9*
- [ ] G10.1 · §6 Step 10 · — · `models/ai_usage.py` (`AIUsageEvent` + `AIUsageRollup`), **UUID PKs (C6)**, migration `0011`, RLS policies, registry entries, entity classification (C9a/c/d/f) · verify: `tests/integration/test_pg_baseline.py::test_baseline_matches_metadata`
- [ ] G10.2 · §6 Step 10 · — · `meter.py::record_usage` — event + rollup increment in **one transaction** · verify: `tests/unit/test_overage.py::test_single_transaction`
- [ ] G10.3 · §6 Step 10 · — · `MeteredProvider` proxying the **full** surface: `__getattr__` for undeclared methods, `__setattr__` for `provider.model = …` (F8) · verify: `tests/unit/test_ai_metering.py::test_wrapper_proxies_undeclared_surface`
- [ ] G10.4 · §6 Step 10 · **A11** · **G-dispatch** — every dispatch path, enumerated from the tree, increments the counter · verify: `tests/unit/test_ai_metering.py::test_all_entry_points_metered`
- [ ] G10.5 · §6 Step 10 · A12,A13 · no provider cached module-level; archiving does not reduce `calls_used` (D18/F10) · verify: `tests/unit/test_ai_metering.py::test_no_module_level_cache`, `::test_archive_preserves_usage`

### [ ] G11 — Step 11: overage behaviour — *dep: G10*
- [ ] G11.1 · §6 Step 11 · A14 · soft cap passes, hard ceiling denies, each nudge fires once · verify: `tests/unit/test_overage.py::test_ceiling_and_nudges`
- [ ] G11.2 · §6 Step 11 · A15 · **N10** — system-initiated calls never metered or denied · verify: `tests/unit/test_overage.py::test_system_calls_exempt`
- [ ] G11.3 · §6 Step 11 · A26 · two concurrent calls at the cap: exactly one proceeds · verify: `tests/unit/test_overage.py::test_concurrent_at_cap`

### [ ] G12 — Step 12: scheduled-job entrypoints — *dep: G7 — MUST precede G13*
- [ ] G12.1 · §6 Step 12 · A16 · `mihomes jobs trial-sweep` / `reconcile`, both idempotent (D15) · verify: `tests/integration/test_jobs.py::test_idempotent`
- [ ] G12.2 · §6 Step 12 · A9 · `reconcile` corrects a deliberately drifted account · verify: `tests/integration/test_reconcile.py::test_drift_corrected`

### [ ] G13 — Step 13: the trial state machine — *dep: G12*
- [ ] G13.1 · §6 Step 13 · A17 · a trial grants Pro entitlements with **no Stripe subscription existing** (F3) · verify: `tests/integration/test_trial.py::test_cardless_trial_entitlements`
- [ ] G13.2 · §6 Step 13 · A18 · one trial per account, ever (`trial_used_at`) · verify: `tests/integration/test_trial.py::test_one_trial_ever`
- [ ] G13.3 · §6 Step 13 · A19 · expiry downgrades and surfaces over-limit, dropping nothing · verify: `tests/integration/test_trial.py::test_expiry_is_nondestructive`

### [ ] G14 — Step 14: downgrade + restricted mode — *dep: G13*
- [ ] G14.1 · §6 Step 14 · A20 · **D9** — surplus read-only, core home editable, **nothing deleted**, all three arrival paths · verify: `tests/integration/test_downgrade.py::test_nothing_deleted`

### [ ] G15 — Step 15: the four emails — *dep: G7, G12*
- [ ] G15.1 · §6 Step 15 · A21 · four template pairs + four `send_*` on the **existing** `EmailService`; do **not** extend the transport-only `EmailProvider` Protocol · verify: `tests/integration/test_billing_emails.py::test_four_templates`
- [ ] G15.2 · §6 Step 15 · A21 · 3 webhook-triggered + 1 scheduler-triggered (`trial_ending`, F3/B2); each fires once per event · verify: `tests/integration/test_billing_emails.py::test_fires_once`

### [ ] G16 — Step 16: the three feature gates — *dep: G8*
- [ ] G16.1 · §6 Step 16 · A22 · ratings gated at **context assembly**, not the route (N11) — `services/vendor.py` live path *and* `vendor_rating.py`'s three dead ones (F6) · verify: `tests/integration/test_feature_gates.py::test_ratings_gated_pages_load`
- [ ] G16.2 · §6 Step 16 · A22 · `dashboard.html` + `property_detail.html` **still load** on Free · verify: same test
- [ ] G16.3 · §6 Step 16 · A23 · `due_date` denied on Free; an **undated** work order succeeds (D13) · verify: `tests/integration/test_feature_gates.py::test_due_date_gate`
- [ ] G16.4 · §6 Step 16 · A24 · **N9/N10** — the Telegram path passes no `due_date` (F5), so no bot path trips the gate · verify: `tests/integration/test_feature_gates.py::test_bot_path_ungated`

### [ ] G17 — Step 17: the importer gate — *dep: G8*
- [ ] G17.1 · §6 Step 17 · A25 · **D16** — assert `can("home.create")` per home; refuse rather than create an over-limit account; no partial account left · verify: `tests/integration/test_importer_gate.py::test_over_limit_refused`

### [ ] G18 — Step 18: reconciliation in anger + the exit criterion — *dep: all*
- [ ] G18.1 · §6 Step 18 · A9 · sweep `reconcile` over all accounts with a Stripe customer; a dropped webhook is corrected within one sweep · verify: `tests/integration/test_reconcile.py::test_drift_corrected`
- [ ] G18.2 · §6 exit · **A31** · **THE exit criterion** — a Free gate flips to Pro **from the webhook, not the redirect** (D1/N1) · verify: `tests/integration/test_upgrade_flow.py::test_exit_criterion`

### [ ] G-Final — Compound-stop verification (conventions §4.1)
- [ ] F.1 · full-suite `pytest -q` green (condition C)
- [ ] F.2 · every §8 criterion green by its own named test (condition E) — all 31
- [ ] F.3a · walk §6 top-to-bottom: every step has a task (condition B, steps)
- [ ] F.3b · walk §8 top-to-bottom: every criterion has a gate (condition B, criteria)
- [ ] F.4 · smoke green (condition D)
- [ ] F.5 · write end-of-run report `tasks/build-loop-spec004-report.md` (§5)

---

## 2. Group-specific gates (conventions §2)

| Group | Gate | Failure class it targets |
|---|---|---|
| **G3, G10** | migration round-trip + autogenerate-clean + `test_baseline_matches_metadata` | reversibility, convergence — **and C6's UUID trap** |
| **G5** | concurrent-delivery test with two real sessions, not a mocked race | the insert-first guarantee is only real under concurrency (N4) |
| **G10** | **G-dispatch derived from the tree** (§0.4) | A11's list-rot — the phase's definition of done |
| **G16** | both dashboard pages render for a Free account | N11 — gating the route would 403 the dashboard |

**Mutation-check every new security/billing arm** before believing it (SPEC-003 lesson): break the
arm, confirm red, restore. Two of SPEC-002's four security arms had no teeth until this was done.
And per SPEC-003's third lesson: **a surviving mutation has three diagnoses** — redundant condition
(delete), untested arm (add the test), inert difference (document with the measurement). Measure
what differs before deciding which.

## 2.1 RUN STATE — where a resuming session picks up

**Steps 1–4 landed.** Suite at **1995 passed, 3 skipped, 2 xfailed, 0 failed** (baseline 1945).
Resume at **G5.1** — idempotency and out-of-order handling, which fills in behind the
`_dispatch` seam G4 left in `services/billing/service.py`.

| Group | State | Commit |
|---|---|---|
| harness + pre-flight | ✅ | `36eca9b` |
| G1+G2 — provider seam, price map | ✅ 21 tests | `d4ae614` |
| G3 — the ledger, A6 carve-out | ✅ 19 tests | `b672571` |
| G4 — webhook route, Host-guard fix | ✅ 10 tests | `682f0e7` |
| G5 onward | ⬜ not started | — |

## 3. Circuit breaker (conventions §3)

Halt and write the report with status `HALTED` if: more than **5** tasks poison, **or** G9/G10
poisons (A11 is the definition of done and G9 is its precondition), **or** G3/G4 poisons (the
webhook path has no idempotency guarantee without them), **or** two consecutive groups fail their
full-suite gate.
