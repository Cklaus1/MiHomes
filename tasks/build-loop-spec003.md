# SPEC-003 Build Loop — Phase 2: Onboarding + Team + RBAC

> **Input spec:** `docs/specs/SPEC-003-phase2-onboarding-team-rbac.md` (857 lines, *Ready to
> build*, **one open decision — O1**)
> **Conventions:** `tasks/build-loop-conventions.md` — stop condition, poison ceiling, circuit
> breaker, artifact routing are defined there and inherited here **unchanged**.
> **Branch:** `worktree-spec-build-harness`. **Target ref:** HEAD `c09c54d` (SPEC-002 complete).
> **Invocation:** `/loop tasks/build-loop-spec003.md`

**Phase 1 defends the boundary *between* customers. Phase 2 defends the boundary *inside* one.**
The spec's own framing, and it sets the failure mode: cross-tenant leakage fails loudly and RLS
backstops it, but a staff member seeing another property's data — or the household's finances —
**looks exactly like the feature working**. There is no backstop below this layer. Every gate in
this harness is built on the assumption that a passing UI is not evidence.

**A15 is the definition of done** (spec §8): *"Roles enforced in the UI while the AI answers
freely is not a partial success — it is the leak wearing the feature's clothes. If A15 is not
green, Phase 2 is not finished regardless of what else works."*

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, `TEST_DATABASE_URL` set | ✅ PostgreSQL 18.x, trust auth on localhost, `mihomes_test` exists |
| P2 | **A separate landing database**, `LANDING_TEST_DATABASE_URL` | ✅ `mihomes_phase0` exists — **see §0.2, this is a real gate, not boilerplate** |
| P3 | SPEC-002 landed: `accounts`, `memberships`, `membership_property_scopes`, `invites`, `sessions`, `TenantOwned`, scoped session | ✅ verified at HEAD — 162 `account_id` hits, `0001_pg_baseline` + `0002_rls` |
| P4 | SPEC-001 `EmailService` (Step 12 needs `welcome`, `staff_invite`, `invite_accepted`) | ⚠️ **verify at G12 pre-flight** — not re-measured here |

**Environment — pass inline, the worktree guard rejects `export` chains:**

```
DATABASE_URL            postgresql+psycopg://postgres@localhost:5432/mihomes_test
MIGRATION_DATABASE_URL  postgresql+psycopg://postgres@localhost:5432/mihomes_test
TEST_DATABASE_URL       postgresql+psycopg://postgres@localhost:5432/mihomes_test
LANDING_TEST_DATABASE_URL  postgresql+psycopg://postgres@localhost:5432/mihomes_phase0
```

Invoke pytest as `py -m pytest`, never `python` (Store shim).

### 0.2 `LANDING_TEST_DATABASE_URL` is a stop-condition dependency, not a convenience

Measured at pre-flight: pointing all URLs at one database yields **4 failed, 1558 passed**. With
`LANDING_TEST_DATABASE_URL` pointed at `mihomes_phase0`, the same four pass (46 passed in the
affected modules). The failures are:

```
test_migration_waitlist.py::test_landing_database_holds_only_the_waitlist_table
test_oauth_stub.py::test_callback_creates_no_users_table
test_waitlist_service.py::test_signup_is_idempotent
test_waitlist_service.py::test_confirm_rejects_expired_token
```

`tests/integration/test_migration_waitlist.py:28-35` documents the mechanism in the code: SPEC-002's
conftest runs `create_all()` over 44 tenant tables against `TEST_DATABASE_URL`, which breaks the
landing module's *"exactly {waitlist, alembic_version_landing}"* assertion. The fallback to
`TEST_DATABASE_URL` exists so a single-database setup still runs — it just does not **pass**.

**SPEC-002's harness never recorded this**, and its report's `1562 passed, 0 failed` is only
reproducible with the landing URL set. A run that starts from the one-database env will read four
red tests as a SPEC-003 regression and burn attempts on them. Set both URLs or the baseline is wrong.

---

## 0.3 Stop condition

Per conventions §0, all five. Conventions §0.1: *"SPEC-003 onward — C is suite green **including
this spec's new tests**."*

| | Condition | For SPEC-003 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 below |
| **B** | every §6 step tasked **and** every §8 criterion gated | F.3a + F.3b |
| **C** | full suite green **including this spec's new tests** | baseline below → final |
| **D** | smoke green | `tests/integration/test_smoke_all_tools.py` — **a Step 10 migration target** (see §0.5) |
| **E** | every §8 criterion green by its own named test | all 33, F.2 |

**Measured baseline — HEAD `c09c54d`, correct env, before any SPEC-003 code:**

```
py -m pytest -q   →   1562 passed, 3 skipped, 2 xfailed, 0 failed   (171s)
```

**This exactly reproduces SPEC-002's reported `1562 passed, 3 skipped, 2 xfailed`** — which is
itself the evidence that §0.2's env fix is correct rather than merely convenient. The same tree
under the one-database env reports `4 failed, 1558 passed`; the four-test delta is entirely
§0.2's landing-DB collision. The 3 skips and 2 xfails are inherited from SPEC-002 and
known-benign; conventions §0 makes **a new skip red** — a skipped test is the most likely way
this harness reports a false success.

### 0.4 Condition E has a hole this spec cannot close by itself — two gates close it

Conventions §0: *"a stub can satisfy A+B+C+D"*, and E binds completion to §8. **But two of
SPEC-003's criteria define their own scope, so E passes vacuously on a faithful implementation:**

- **A12** — *"money is redacted for staff on every `REDACTED_FIELDS` model"*. `REDACTED_FIELDS` is
  the dict under test. Implement §4.4 verbatim and A12 is green while seven real money columns
  leak (§0.6). The test cannot fail, because the thing being tested supplies the test's scope.
- **A15** — *"…via any of the 15 executors"*. A hand-written list of 15 passes forever; a 16th
  executor added later is unscoped and untested.

**Two derived gates, both mandatory, both independent of the spec's transcription:**

| Gate | Check | Closes |
|---|---|---|
| **G-census** | Every `Money`-typed column on every mapped model is either in `REDACTED_FIELDS` or in an explicit `MONEY_VISIBLE_TO_STAFF` allowlist **with a one-line reason per entry**. A new money column fails the suite until classified. | A12's circularity |
| **G-exists** | Every string in `REDACTED_FIELDS` resolves to a real mapped attribute or relationship on its model. | five names in §4.4 that do not exist (§0.6) |

A15's test is **parameterised over `_EXECUTORS.keys()` read from `tools.py` at test time**, never
a literal list. Same principle as the census: derive the gate from the code, not from a
transcription of it. This is conventions §4.1's F.3b applied inside a step.

### 0.5 D's smoke file is a Step 10 target

`tests/integration/test_smoke_all_tools.py` invokes all 15 executors. Step 10 changes every
executor's signature to take a **required** scope (§4.3, N2 — *"a forgetting call site fails to
import"*). The smoke file **is** a forgetting call site. Expect it red the moment G10 lands the
signature and green when G10 finishes; do not read that as regression, and do not soften the
required-positional rule to keep it green — that would reintroduce F3's footgun.

---

## 0.6 PRE-FLIGHT RE-VERIFICATION (conventions §3.1) — measured at HEAD `c09c54d`

SPEC-003 was written **2026-08-03 against a tree where SPEC-002 did not exist** (its own §0.1:
*"Phase 1 is not built… `account_id` appears zero times"*). That premise is now false, and §0.1
states the consequence: *"If SPEC-002's implementation diverges from its spec, this document
inherits the divergence. Re-verify §4 and §5 against the tree before building."* This section is
that re-verification. **Corrected values are authoritative over the spec's prose.**

### Claims that hold

| Claim | Source | Measured |
|---|---|---|
| **146** route decorators | F1 | **146** ✓ |
| `assets.py` 18, `work_orders.py` 13, `properties.py` 10, `ai.py` 10 | F1 | 18 ✓, 13 ✓, 10 ✓, 10 ✓ |
| **15** AI tool executors (`_query_*` defs == `_EXECUTORS` entries) | F3 | **15** ✓ |
| `require_permission` — zero hits in `src/` | F6 | **0** ✓ |
| `entitlement` — zero hits in `src/` | F6 | **0** ✓ |
| `AuditLog.actor` exists, defaults `"admin"` | F6 | ✓ `String(100)`, and the model is **already `TenantOwned`** |
| `configurations` PK is `(account_id, key)` | F7 | ✓ SPEC-002 landed it |
| next migration is `0003_phase2_rbac` | §3 | ✓ `0001_pg_baseline`, `0002_rls` present |

### Claims corrected — build against the right-hand column

| # | Claim | Spec | **Measured at HEAD** |
|---|---|---|---|
| **C1** | router files | "23 files" (F1) | **24** — `auth.py` (4 routes) is new from SPEC-002 G12 |
| **C2** | `vendors.py` route count | 11 (F1) | **9** |
| **C3** | `assemble_context` signature | `(session, *, property_slug=None)` (§4.3) | `(session, roles, query, *, property_slug=None, session_id=None, max_tokens=50000)` |
| **C4** | `_EXECUTORS` map location | `tools.py:919-935` (F3) | `:922` |
| **C5** | `execute_tool` location | `tools.py:274` (F3) | `:292` |
| **C6** | `AuditLog.actor` location | `audit_log.py:25` (F6) | `:33` |
| **C7** | `account_id` appears zero times | §0.1 | **162 hits** — superseded by SPEC-002 |

**C1/C2 note for the reviewer:** the 146 total is unchanged *by coincidence*, not because nothing
moved — `vendors.py` lost 2 and `auth.py` added 4. Do not read "146 ✓" as "the census is stable."

**C3 is load-bearing.** §4.3's "AFTER" signature is written against a 2-argument function that
does not exist. The rule it encodes — **required, positional, no default** (N2) — is what must be
preserved, not the literal line. Scope is inserted as a required positional; a call site that
forgets it raises `TypeError` at call time rather than silently receiving full-account access.

### C8 — `REDACTED_FIELDS` (§4.4) names **six** fields that do not exist

Verified against the models. A `frozenset` of nonexistent names **redacts nothing, silently** —
and A12 still passes, because A12's scope is that same dict (§0.4).

| Model | §4.4 says | Reality |
|---|---|---|
| `WorkOrder` | `cost`, `estimated_cost`, `actual_cost`, `invoice_number` | `estimated_cost` ✓, `actual_cost` ✓ · **`cost` ✗, `invoice_number` ✗** |
| `Asset` | `value`, `purchase_price`, `price_entries` | `purchase_price` ✓, `price_entries` ✓ · **`value` ✗** · **missed: `replacement_cost_estimate`** |
| `Consumable` | `unit_price`, `last_order_cost` | `unit_price` ✓ · **`last_order_cost` ✗** |
| `Contract` | `cost`, `billing_frequency` | ✓ ✓ |
| `Vendor` | `insurance_info`, `license_number`, `notes`, `ratings` | ✓ ✓ ✓ · **`ratings` ✗** — `VendorRating.vendor` carries no `back_populates`, so the reverse attribute was never created |
| `Task` | `estimated_cost` | **✗ — `Task` has no money column at all** (`estimated_hours` is `Float` hours) |

**Correction:** drop `Task` from `REDACTED_FIELDS` rather than inventing a field to match the
spec; add `Asset.replacement_cost_estimate`. Gate **G-exists** (§0.4) makes this class of error
impossible to reintroduce.

**`Vendor.ratings` was found at G2, not at pre-flight** — the earlier pass flagged it "verify at
G8" rather than measuring it, and it turned out to be the sixth missing name. Recording the miss
because it is the same mistake in miniature: a name deferred is a name unchecked. **D12's ratings
clause is still enforced**, but by *row-denial* — `VendorRating` is `ACCOUNT_LEVEL`, so staff
never receive the row — not by field redaction. "The field is absent from the list" and "the data
is protected" are different claims and only the second is true here.

### C9 — the `Money` census: 15 columns, §4.4 covers 6 of them

```
Asset.purchase_price            Asset.replacement_cost_estimate   PriceEntry.price
Budget.amount                   Transaction.amount (budget.py:83) Consumable.unit_price
ConsumablePriceEntry.price      Contract.cost                     Event.budget
Insurance.coverage_limit        Insurance.deductible              Insurance.annual_premium
RecurringExpense.amount         WorkOrder.estimated_cost          WorkOrder.actual_cost
```

**`Event.budget` is the sharp one.** §4.1 classifies `event` as **property-scoped** — staff *may
see the row* — and it carries a money column §4.4 never mentions. That is F4's exact shape
(*"money lives inside rows staff are permitted to see"*), missed by the very table written to
close it. `Insurance` is money-bearing, property-scoped, and **absent from §4.1 entirely** (C10).

Columns on models staff never reach at all (`Budget`, `Transaction`, `RecurringExpense`) still
require an explicit `MONEY_VISIBLE_TO_STAFF` entry or a row-level denial recorded in the entity
classification — **G-census fails on unclassified, not on unredacted.** Silence is the bug.

### C10 — §4.1's entity classification is incomplete, and N4 forbids that

N4: *"Every model must land in one §4.1 class."* **The tree has 42 mapped classes**
(`Base.registry.mappers`); §4.1 names about 22. Twenty appear in no class:

```
InsurancePolicy  Alert      VendorRating   StaffPTORequest  AIConversation
Tag              Template   Property       Account          Membership
Invite           AuditLog   Session        TagAssignment    TemplateItem
PriceEntry       ConsumablePriceEntry       Guest           EventGuest
MembershipPropertyScope
```

*(Counted as **mapped classes**, not model files — an earlier pass of this pre-flight said
"thirteen" by counting files, which undercounts every module holding more than one class:
`budget.py` alone carries `Budget` and `Transaction`. The classification test enumerates
mappers, so mappers are the unit that matters.)*

**`PriceEntry` and `ConsumablePriceEntry` are the two that bite.** Both carry a `Money` column
(C9) and both are children of a property-scoped parent staff may see, so an unclassified child is
a money leak reached one relationship hop from a permitted row — `Asset.price_entries` is in
§4.4's redaction list precisely because that hop exists, yet the entry model itself is nowhere in
§4.1.

`insurance` (money-bearing, property-scoped) and `vendor_rating` (D12 denies staff ratings
explicitly, yet the model is unclassified) are the two that can leak. **Step 1 gets a test that
enumerates every `Base` subclass and asserts each lands in exactly one class — fail-closed on
unclassified.**

**Also corrected:** §4.1's *rationale* for the account-level class is wrong even where its
*outcome* is right. `budget`, `contract`, and `recurring_expense` **do** carry `property_id`, so
"no property to scope by" is false for all three. Deny-for-staff stays; the reason changes to a
policy decision, and the classification test must not infer class from the presence of
`property_id`.

### C11 — `Document` has no `property_id`; D13/Step 9 assumes one

`models/document.py` carries `entity_type` / `entity_id` (polymorphic) and **no `property_id`**.
Step 9 says staff queries filter on `staff_visible` *and* property scope — there is no column to
scope by. The spec never anticipated this. **Assumption, stated rather than silently chosen:**

> **Resolve scope through `entity_type`/`entity_id` to the parent row's `property_id`. A document
> with `entity_id IS NULL` is account-level and is invisible to staff regardless of
> `staff_visible`.** Fail-closed, consistent with D3 and D13's "default false", and it needs no
> schema change beyond `staff_visible`.

Rejected alternative: adding `documents.property_id`. It denormalises, needs a backfill, and
leaves the two sources of truth to drift. Revisit only if a document must be property-scoped
without a parent entity.

### C12 — HIDDEN PREREQUISITE: nothing binds tenant context to a web request

**This is the finding that changes the DAG, and it is absent from §6's step list.**

`require_permission(user, current_account, action, target_property)` (§5) needs a request-scoped
user and account. Measured:

- `web/deps.py` defines **only `get_db`** — no auth dependency.
- `account_context()` is entered in exactly two places: `cli/__init__.py:81` and the **test
  fixtures** (`tests/conftest.py:174, 360, 411).
- `lookup_session()` has exactly **one** call site in `src/`: `web/routes/auth.py:191`, inside
  `signout_everywhere`. No ordinary route resolves the session cookie.
- `current_account` / `current_user` are `ContextVar`s with no default; `require_account()` raises
  `LookupError` when unset — by design (SPEC-002 §4.4, fail-closed).

**Consequence:** the web app has sign-in and a session store but **no per-request binding**. Web
tests pass because conftest binds the account around them. `require_permission` has no source for
its first two arguments, and Steps 2, 4, 5, 7, 9, 13, and 15 are unbuildable as written until it
exists. This is not a SPEC-002 defect — Phase 1 shipped tenant scoping and sign-in; wiring the
authenticated request is where Phase 2 begins.

**→ New group `G0`, before everything.** It carries no §8 criterion (it discharges none) and no
§6 step, so **F.3a/F.3b must not flag it as an unmapped task** — it is recorded here as a
pre-flight-discovered prerequisite, per conventions §3.2.

### C14 — `require_permission`'s signature, and whether it re-reads the role (decided at G2, blocks G3)

§5 writes `require_permission(user, current_account, action, target_property=None)`. That
argument list cannot reach `scoped_property_ids(session, membership)`: it has neither a session
nor a membership. Same situation as C3 — **the rule §5 encodes is the five ordered steps, the
404-not-403 outcome (D9), and the route-class behaviour; the argument list is not the rule.**

```python
def require_permission(session, principal: RequestPrincipal, action: str,
                       target_property: Property | UUID | None = None) -> None
```

`RequestPrincipal` (G0) already carries `user_id`, `account_id`, `membership_id`, and `role`, so
it supplies both of §5's first two arguments and the membership the scope primitive needs.

**Does it re-read the `Membership` row per call?** No — and the reason must be written down,
because a later reader will "fix" it in one direction or the other. D8/N10 requires the role be
loaded **fresh from the DB every request**, never cached in the session. `_resolve_authenticated`
does exactly that, once per request, and `test_revoked_membership_not_resolved` proves it.
Re-reading inside `require_permission` would be fresh-per-*call* — stricter than D8 asks, and a
database round trip on every authorization check, of which a single page render performs many.
Trusting the principal is correct **because the dependency resolved it this request**; that
conditional is the whole justification, so any future call site that constructs a
`RequestPrincipal` from anything other than a live request breaks D8.

### C15 — A5's "the allowlist only ever shrinks" needs a mechanism (blocks G5)

A5 is a claim about **history**, and a test sees only the current tree. Two mechanisms exist:
pin a committed ceiling as a literal and assert `len(SHRINKING_ALLOWLIST) <= CEILING`, or diff
against the merge-base with git. **Take the ceiling**: it is self-contained, works in a shallow
CI checkout, and lowering it is a visible act in the diff rather than an invisible property of
the environment.

**Consequence for the DAG:** G5.2's assertion only becomes meaningful once G6 starts lowering the
ceiling, so **G5.2 and G6.9 are two halves of one gate**. G6.9 must assert `CEILING == 0` **and**
`len(SHRINKING_ALLOWLIST) == 0` together — otherwise the list can be emptied while the ceiling
sits at its starting value, and the monotonic test passes forever without ever having constrained
anything.

### C13 — bug found during pre-flight, outside the DAG → `opportunities.md`

`MembershipPropertyScope` (`models/membership.py`) assigns `__table_args__` **twice** — line 77
(two indexes) and line 95 (one `UniqueConstraint`). The second binding wins, so
`ix_scopes_account_membership` and `ix_scopes_account_property` were **never created**; confirmed
absent from `0001_pg_baseline.py`, which contains only `uq_scope_membership_property`.

**Severity: low — performance only, not an isolation hole.** `scoped_property_ids()` queries by
`membership_id`, which the surviving unique constraint's leading column covers. Logged, not fixed
here (conventions §6: *"new-scope bugs go to `opportunities.md`, never silent side-fixes"*).
A `[BUG][low]` line is appended at G0.

---

## 0.7 O1 — the one open decision, and why it does not block the run

Conventions §3.3: classify each `O` as **blocks-build** or **blocks-ship**; poison only on
blocks-build. **O1 (at-rest encryption for AI provider API keys) is blocks-build for exactly one
task and blocks-ship for nothing else.**

The spec has already done the split for us — §1.3: *"**Blocks Step 15's write path only** — the
read/masking half can proceed"*, and N11: *"Do not write secret config values to a plaintext
column from a new web form until O1 is answered. The masking half of Step 15 proceeds."*

So **G15 splits in two**: `G15.1` masking/read (build now, discharges A27) and `G15.2` the secret
**write** path (`[!]` + `[BLOCKED]` on arrival, no attempts spent). Recorded as an unmet launch
gate in §0.8 and the end-of-run report. **Do not route this to the user mid-run** — N11 already
authorises the split.

## 0.8 UNMET LAUNCH GATES — carried forward, not silently satisfied

Conventions §3.3. Inherited from SPEC-002's report plus this phase's own.

| # | What is off / unresolved | Owner |
|---|---|---|
| **U1** | **O1** — provider API keys stay plaintext in `configurations.value`. Step 15 masks on **display** and does not make them safe (spec §10). | founder |
| **U2** | **Mis-declared actions.** Step 4's harness proves every route declares *something*, not the *right* thing. `task.manage` on a contract-delete route would pass (spec §10). Mitigation is human review of every **write/delete/export** route, listed in the PR — **not** a gate this loop can close. | human review |
| **U3** | **Aggregate inference.** A15 tests direct paths; a staff member can still sometimes infer account-level facts from what they may see (spec §10). Accepted. | accepted |
| **U4** | **Bot transport.** Step 16 scopes *answers*; the bot still polls with a token in per-account config as a supervised CLI process, not the authenticated webhook `TELEGRAM_PRD` §5 describes. Phase 4+ (spec §10, N7). | Phase 4+ |
| **U5** | Inherited from SPEC-002: **S1 archival** (unowned, needs a retention decision), **S7 demo mode broken**, **S5 polymorphic-table drift is app-only**. | founder / accepted |

---

## 1. Task DAG

Conventions §1.3: **one step per group by default**; the group commit is the resume point. Format
is conventions §4: `checkbox + ID · spec-ref · criteria · imperative · verify:`.

**Ordering departure from §6, stated once:** the spec numbers the scope primitive **Step 6**, but
`require_permission` (**Step 2**) must deny an out-of-scope target, which *is* `scoped_property_ids()`.
Building Step 2 first would mean either a second scope implementation (§4.3: *"written separately
they drift, and drift is a leak"*) or a stub. **Step 6's primitive therefore lands as G2, before
Step 2's G3.** `redact_for_role()` is defined in the same group (pure function, no dependencies)
and *applied* at G8, which is what §6 Step 8 actually asks for.

**Migration departure:** §3's manifest names one file, `0003_phase2_rbac.py`, carrying four
unrelated changes that land in four different groups (audit actor → G3, onboarding_state → G11,
documents.staff_visible → G9, telegram_links → G16). One file spanning four commits is not
resumable. **Each group ships its own revision in the chain** (`0003…`, `0004…`, `0005…`,
`0006…`), per §6's own *"independently verifiable and separately committable."*

### [x] G0 — Request-scoped auth dependency — *pre-flight prerequisite (C12), no §6 step, no §8 criterion* — *`1565 passed, 3 skipped, 2 xfailed` (1562 → 1565, +3); commit `<G0>`*
- [x] G0.1 · C12 · — · FastAPI dependency resolving the session cookie → `AuthenticatedSession` → binds `account_context(account_id, user_id)` for the request and yields `RequestPrincipal` (user, account, **membership_id**, role); unauthenticated → 401, authenticated-without-account → 403 (G13 turns it into the picker redirect) · verify: `tests/integration/test_request_context.py::test_request_binds_account_context` ✓
- [x] G0.2 · C12 · — · a request with a revoked membership resolves to **no** membership (D8 — fresh from the DB every request, never cached in the session) · verify: `tests/integration/test_request_context.py::test_revoked_membership_not_resolved` ✓
- [x] G0.3 · C13 · — · append the `__table_args__` bug + the C8/C9/C10 corrections to `opportunities.md` · verify: file contains a `[BUG][low]` line for `membership.py` ✓ 7 entries

> **The dependency must be `async`, and this is load-bearing rather than stylistic.** FastAPI runs
> a *sync* dependency's body in a threadpool (`contextmanager_in_threadpool`), so a
> `ContextVar.set()` inside one applies to the worker thread's copy of the context and is
> discarded before the endpoint runs. **Mutation-verified:** changing `async def` to `def` turns
> `test_request_binds_account_context` from green to a 500 — `LookupError` on an unset
> `current_account`, raised inside the route. The end-to-end probe exists because the sync
> version fails only at runtime and only on paths that touch tenant data.

### [x] G1 — Step 1: action vocabulary + the matrix as data — *dep: none* — *`1583 passed` (1565 → 1583, +18); 4 arms mutation-verified*
- [x] G1.1 · §6 Step 1 · A1 · `authz/actions.py`: 21 keys covering all 20 `ONBOARDING` §9.2 rows, `Grant`/`Access`/`ActionSpec` per §4.1 verbatim · verify: `tests/unit/test_matrix.py::test_all_twenty_rows_covered` ✓
- [x] G1.2 · §6 Step 1 · A2 · R1 — an admin may change neither the active owner's role nor their own; the owner may change anyone's except their own (D2) · verify: `tests/unit/test_matrix.py::test_rule_change_role` ✓
- [x] G1.3 · §6 Step 1 · A3 · R2 — linking a gateway grants no additional data access; link is self-only for every role · verify: `tests/unit/test_matrix.py::test_rule_link_self` ✓
- [x] G1.4 · §6 Step 1 · C10 · entity classification covering **all 42 mapped classes** (§0.6 C10); fail-closed on unclassified; class is **not** inferred from `property_id` · verify: `tests/unit/test_matrix.py::test_every_model_is_classified` ✓

> **Mutation-verified, because tests and module were written in one pass and never observed red.**
> Four independent mutations, each caught by exactly its intended test: dropping `Alert` from
> `ENTITY_CLASSES` → `test_every_model_is_classified`; widening `task.manage` staff to `ALLOW` →
> `test_owner_is_never_weaker_than_admin_or_staff`; deleting R1's admin-cannot-touch-owner clause
> → `test_rule_change_role`; renumbering row 20 → `test_all_twenty_rows_covered`.
>
> **A gate whose result depended on collection order, found and fixed here.**
> `test_every_model_is_classified` passed alone and failed in the full unit suite:
> `Base.registry` is process-global and `tests/unit/test_slug.py:25` declares a `DummyModel` on
> it. Now filtered to `__module__.startswith("mihomes.models")`, with a second assertion that the
> filter still covers ≥42 models — otherwise a refactor moving models out of that package would
> silently shrink the gate to nothing while every test still passed.

### [x] G2 — Step 6: the scope primitive + `redact_for_role` — *dep: G1 — moved ahead of Step 2, see above* — *24 tests; 3 arms mutation-verified*
- [x] G2.1 · §6 Step 6 · A10 · `authz/scope.py::scoped_property_ids(session, membership)`; staff with zero scope rows → `frozenset()` (D3, fail closed) · verify: `tests/unit/test_scope.py::test_empty_scope_is_empty` ✓
- [x] G2.2 · §6 Step 6 · A11 · owner/admin → every property in the account, **even with scope rows present** (`ONBOARDING:44`) · verify: `tests/unit/test_scope.py::test_privileged_ignores_scope_rows` ✓ (owner + admin)
- [x] G2.3 · §6 Step 6 · — · `authz/redact.py::redact_for_role` + `REDACTED_FIELDS` **as corrected by C8** · verify: `tests/unit/test_redaction.py::test_redact_is_identity_for_privileged` ✓
- [x] G2.4 · §0.4 · — · **G-exists**: every name in `REDACTED_FIELDS` resolves to a real mapped attribute/relationship · verify: `tests/unit/test_redaction.py::test_every_redacted_field_exists` ✓
- [x] G2.5 · §0.4 · — · **G-census**: every `Money` column is redacted, **row-denied by its entity class**, or allowlisted with a reason; unclassified → fail · verify: `tests/unit/test_redaction.py::test_money_census_is_complete` ✓

> **The census's third arm is derived, not hand-listed.** A money column is admissible if it is
> redacted, *or* if `ENTITY_CLASSES` puts its model in a class staff never receive rows from, *or*
> if `MONEY_VISIBLE_TO_STAFF` names it with a reason. Deriving the row-denied arm from the
> classification is what stops the two tables drifting apart — and
> `test_property_scoped_money_is_actually_redacted` closes the obvious escape, which is to silence
> the census by reclassifying a model instead of redacting its money.
>
> **Mutation-verified:** removing `Event.budget` from `REDACTED_FIELDS` — i.e. implementing §4.4
> exactly as written — fails both the census and the property-scoped gate. That is the A12
> circularity closing: the spec's own dict ships the leak, and the derived gate catches it.
> Adding a nonexistent `WorkOrder.invoice_number` fails G-exists; deleting the `status != active`
> check fails both revocation tests.
>
> **`RedactedView` is a wrapper, not in-place nulling.** Setting the attributes to `None` on the
> ORM object would enqueue an UPDATE and the next flush would write those nulls to the database —
> a display concern turned into permanent data loss. It also refuses writes, so a staff-facing
> view can never become a path back onto the row. An **unrecognised role is treated as staff**,
> which D16 relies on directly.
>
> **Redaction is transitive, and that is not gold-plating — a flat proxy leaks through the exact
> call shape Step 10 is built on.** `_query_work_orders` (`services/ai/tools.py:530`) renders
> `wo.vendor.company_name` and `wo.property.name`. A proxy redacting only the work order returns
> a **raw** `Vendor` from `wo.vendor`, carrying the `insurance_info` and `license_number` D12
> denies staff. Mutation-verified: disabling the transitive wrap fails
> `test_related_object_is_redacted_too`, and the assertion output is the leak itself —
> `<Redacted WorkOrder>.vendor` → `license_number = 'LIC-9'`. `_has_redactable_relationships`
> extends the same reasoning to models with no money of their own: `Issue` is not in
> `REDACTED_FIELDS`, but `Issue.work_order.actual_cost` must not be reachable in the clear.

### [x] G3 — Step 2: `require_permission` + audit + the two route classes — *dep: G0, G2* — *29 tests; 3 arms mutation-verified*
- [x] G3.1 · §6 Step 2 · A6 · §9.4's five ordered steps; a `SCOPED` **item** route with `target_property=None` denies · verify: `tests/integration/test_permissions.py::test_item_route_requires_target` ✓
- [x] G3.2 · §6 Step 2 · A7 · a `SCOPED` **collection** route returns filtered rows, **not 403** (N5) · verify: `tests/integration/test_permissions.py::test_collection_route_filters` ✓
- [x] G3.3 · §6 Step 2 · A8 · a cross-account target yields **404**, never 403 (D9 — do not reveal existence) · verify: `tests/integration/test_permissions.py::test_cross_account_is_404` ✓ (+ `test_nonexistent_target_is_also_404` — if only one were 404 the *pair* would still leak)
- [x] G3.4 · §6 Step 2 · A9 · revoking a membership denies on the **next request** (D8/N10 — no session cache) · verify: `tests/integration/test_permissions.py::test_revocation_immediate` ✓ **request-level by design, see C14**
- [x] G3.5 · §6 Step 2 · A33 · `authz/audit.py`; **every deny is an audit event**; real actor, never the `"admin"` default (F6) · verify: `tests/integration/test_audit.py::test_denies_and_actor` ✓
- [x] G3.6 · §6 Step 2 · A33 · **no migration needed — §3's manifest is wrong here.** `audit_log.actor` is already `String(100)` with a *Python-side* default, so removing it emits no DDL (`server_default is None`, verified). "Widening" was unnecessary; the fix was behavioural. · verify: column inspection ✓

> **The deny audit needed its own transaction, and finding that out took writing the test twice.**
> FastAPI's `get_db` does `except Exception: s.rollback(); raise`, and `HTTPException` **is** an
> `Exception` — so a deny audit written through the request session is destroyed by the very
> mechanism that reports the denial. A33's *"every deny writes a row"* would be false in
> production while passing any in-transaction test. Denies now commit on an independent
> connection; **successes** stay in the caller's session, so an audit row can never describe a
> change that did not land.
>
> **The first version of that durability test passed against broken code and failed against
> correct code.** It bound the "independent" factory to the test's own connection, so the write
> landed in a savepoint inside the transaction it was supposed to survive. A fixture that makes a
> correct implementation look wrong is worse than no test — it argues for removing the
> independence that makes A33 true.
>
> **A33's other half was one function, not 73 edits.** F6: all 73 `record_change` call sites
> across 20 services write the fictional `"admin"`. Those services do not know who is acting and
> should not have to — the request does, and `current_user` is already bound per request by G0
> and per command by the CLI. `resolve_actor()` reads it, fixing all 73 without touching one.
> Unattended paths log `"system"`: true where `"admin"` was a guess, and visibly not a user id.

### [x] G4 — Step 3: entitlements service — *dep: G1* — *25 tests*
- [x] G4.1 · §6 Step 3 · A25 · `entitlements/limits.py` (one source of truth, rule 1) + `can()` per `PRICING` §3.2 rules 1–5; every `Denied` names an `upgrade_target` (rule 4) · verify: `tests/unit/test_entitlements.py::test_denied_names_target` ✓
- [x] G4.2 · §6 Step 3 · A26 · RBAC and entitlements are **independent** gates, both must pass (D10) · verify: `tests/unit/test_entitlements.py::test_both_gates_required` ✓ (both directions, + `can()` structurally cannot see a role)
- [x] G4.3 · §6 Step 3 · — · `usage()` declared, returns `{used: 0, limit: None, resets_at: None}`, tagged `DEFERRED (Phase 3)` (P3-b, N9) · verify: `tests/unit/test_entitlements.py::test_usage_is_declared_only` ✓
- [x] G4.4 · §6 Step 3 · — · `can()` **called server-side** at property creation, with the count taken inside the caller's transaction (rule 5) · verify: `tests/unit/test_entitlements.py::TestCanIsActuallyCalled` ✓ — **the invite half lands with G12**, where `invite_service` and A19's seat race live

> **Two limits tables, and the second one is the whole of D18.** `PRICING` §3.1 is the *Phase 3*
> table (`max_homes: 1`, `staff_invites_allowed: false`). Phase 2 makes every account `free` (D7),
> so shipping those as active would gate the product for **everyone** with no paid tier to upgrade
> to — §7's deferred table says the config *"simply says free, unlimited."* So `PLAN_LIMITS` is
> permissive, `PLAN_LIMITS_PHASE3` carries the real numbers inert, and a test asserts the two
> declare identical key sets so Phase 3's swap is one line and cannot silently drop a key.
>
> **This is also what makes A25 testable without breaking D18.** With the active table `can()`
> never denies — correct, and required by N8 — so testing rule 4 against it would be vacuous. The
> denial machinery is exercised against the Phase 3 table instead, and a separate test pins that
> the *active* table still gates nothing.

### [ ] G4 — Step 3: entitlements service — *dep: G1*
- [ ] G4.1 · §6 Step 3 · A25 · `entitlements/limits.py` (one source of truth, rule 1) + `can()` per `PRICING` §3.2 rules 1–5; every `Denied` names an `upgrade_target` (rule 4) · verify: `tests/unit/test_entitlements.py::test_denied_names_target`
- [ ] G4.2 · §6 Step 3 · A26 · RBAC and entitlements are **independent** gates, both must pass (D10) · verify: `tests/unit/test_entitlements.py::test_both_gates_required`
- [ ] G4.3 · §6 Step 3 · — · `usage()` declared, returns `{used: 0, limit: None, resets_at: None}`, tagged `DEFERRED (Phase 3)` (P3-b, N9 — **do not build a meter**) · verify: `tests/unit/test_entitlements.py::test_usage_is_declared_only`
- [ ] G4.4 · §6 Step 3 · — · `can()` is actually **called server-side** at invite creation and property creation · verify: `tests/unit/test_entitlements.py::test_can_is_called_at_call_sites`

### [x] G5 — Step 4: the fail-closed route harness — *dep: G3 — MUST precede G6 (N1)* — *9 tests; teeth mutation-verified*
- [x] G5.1 · §6 Step 4 · A4 · `authz/declare.py::@declares(action, access)` + a test walking the FastAPI router table, failing on any endpoint lacking `(action, route_class)` · verify: `tests/unit/test_route_declarations.py::test_no_undeclared_routes` ✓
- [x] G5.2 · §6 Step 4 · A5 · two allowlists — **permanent** (`auth` only, justified) and **temporary** (per-module, C15); ceiling pinned and asserted equal to the list length · verify: `tests/unit/test_route_declarations.py::test_allowlist_monotonic` ✓
- [x] G5.3 · §6 Step 4 · A4 · an undeclared route **makes the suite fail** (the harness has teeth) · verify: `tests/unit/test_route_declarations.py::test_scratch_route_is_caught` ✓ — and mutation-verified end-to-end: removing `@declares` from `dashboard.py` fails `test_no_undeclared_routes`, naming `GET /  (mihomes.web.routes.dashboard)`

> **The declaration lives on the endpoint, not in a central registry.** A registry keyed on
> `(method, path)` drifts the moment a path is edited, and fails in the *worst* direction when it
> does: the renamed route sails through unmapped while the harness reports a missing declaration
> for a route that has one. An attribute set by a decorator cannot desynchronise from its
> function. Unknown action keys raise at **import time**, which is the literal content of §9.2's
> *"deploy-time error, not a silent allow"*.
>
> **The temporary allowlist is per-module, not per-route (C15).** G6 works file by file, so the
> module is the unit of work: 24 entries going to 0 rather than 146. Partial work *within* a
> module still fails — a half-declared router is not a safe state — and a 24-line literal stays
> readable in a diff where a 146-line one would not.
>
> **`test_the_harness_sees_the_whole_router_table` is a guard on the guard.** Every other test
> here would pass against an empty route list, so the walk asserts it finds ≥140 routes. Same
> shape as G1's model-count floor and G2's `money_columns()` floor: a derived gate needs a check
> that it is still deriving from something.
>
> **`dashboard.py` was declared here rather than in G6**, as the mechanism's end-to-end proof:
> declare → drop from the allowlist → lower the ceiling → still green, and red the moment the
> declaration is removed. CEILING 23 → 22.

### [ ] G6 — Step 5: declare actions on the remaining router modules — *dep: G5 — N1: chunked, never one task*
> **The real scope is 142 decorators across 23 modules, not 146 across 24.** Pre-flight measured
> 146/24 *including* `auth.py`, which is now `PERMANENT_ALLOWLIST` (4 routes) — so it is not a G6
> target. **8 routes done** (`dashboard` 1 at G5, `tasks` 7), **134 across 21 modules remain**.
> **`CEILING` in `tests/unit/test_route_declarations.py` is the progress counter** — currently
> **21**, and it must reach 0 at G6.9. One sub-task per router module; the temporary allowlist is
> the continuous gate. **U2 stands:** this catches *undeclared*, not *mis-declared*.
- [x] G6.1 · §6 Step 5 · A4 · `assets.py` (18) ✓
- [x] G6.2 · §6 Step 5 · A4 · `work_orders.py` (13) ✓
- [x] G6.3 · §6 Step 5 · A4 · `properties.py` (10) ✓
- [x] G6.4 · §6 Step 5 · A4 · `ai.py` (10) ✓ — **the `mihomes ai setup` hint moves to G15**, not deleted here: §3 lists it under this phase but its replacement *is* Step 15's settings UI, and removing it now would leave a worse message than the wrong one. (Also `:48-49`, not §3's `:47-48` — line drift.)
- [x] G6.5 · §6 Step 5 · A4 · `issues.py` (9), `inventory.py` (9), `vendors.py` (9 — **not 11**, C2) ✓
- [x] G6.6 · §6 Step 5 · A4 · `contracts.py` (8), `staff.py` (7), `tasks.py` (7), `recurring.py` (7) ✓
- [x] G6.7 · §6 Step 5 · A4 · `calendar.py` (6), `alerts.py` (5), `documents.py` (4), `templates_route.py` (4) ✓ — **`auth.py` is `PERMANENT_ALLOWLIST`, not a G6 target**: sign-in and the OIDC callback run *before* an identity exists, so requiring a declared action would make authentication depend on being authenticated
- [x] G6.8 · §6 Step 5 · A4 · `budget.py` (3), `books.py` (3), `playbooks_route.py` (3), `search.py` (2), `weather.py` (2), `dashboard.py` (1), `library.py` (1), `documents_download.py` (1) ✓
- [x] G6.9 · §6 Step 5 · A5 · allowlist **empty**, `CEILING = 0` ✓ · verify: `test_route_declarations.py::test_allowlist_monotonic` + `test_ceiling_is_not_slack`

> **Census on the real router table: 146 `APIRoute`s, 142 declared, 4 undeclared — and the 4 are
> exactly `auth.py`'s.** That is the corrected C1/C2 arithmetic confirmed from the code rather
> than from the spec's prose.
>
> Distribution: `inventory.manage` 38, `issue.manage` 23, `finance.view` 22, `task.manage` 20,
> `ai.use` 10, `vendor.manage` 8, `property.view` 7, `member.manage` 7, `property.edit` 3,
> `property.add` 2, `property.delete` 1, `vendor.view_contact` 1. **111 write/delete routes** —
> that list is U2's human-review scope and is reproducible from `authz/declare.py`'s attributes.
>
> **Only one route carries `vendor.view_contact`, and that is D12 working.** Row 8 was split in
> two precisely so staff read the vendor *index* and write nothing; every other vendor route is
> `vendor.manage`, which is `DENY` for staff.
>
> **Three declarations are approximations, logged rather than hidden** (`opportunities.md`): the
> 21-key vocabulary has no key for HR records (`staff.py` — F2d says row 10 governs *memberships*,
> not HR data, and §4.1's `PERSONNEL` "own record only" rule is unexpressible), for the library
> (`books`/`library` — §4.1 says account-level, but `Book` carries `property_id` and the library
> is functionally inventory), or for account-level operational config
> (`templates`/`playbooks`). These are the concrete instances of the residual §10 already admits.
>
> **And one real gap found by declaring carefully:** `complete_work_order` *writes*
> `WorkOrder.actual_cost` under `issue.manage`, which is `SCOPED` for staff — so a housekeeper can
> set a cost that redaction then hides from them. Declaring `finance.view` instead would deny
> staff the ability to complete work at all, which D14 explicitly rejects. **D14 covers reads; the
> spec never addresses money *writes* by staff.** The three price-entry route groups were
> deliberately declared `finance.view` for exactly this reason.

### [x] G7 — Step 7: staff scoping in web queries — *dep: G6* — **enforcement goes live here** — *8 tests; 1676 passed*
- [x] G7.1 · §6 Step 7 · — · filter at the **query layer**, never post-hoc (§9.4 step 4); a scoped staff `GET /tasks` returns only scoped rows · verify: `tests/integration/test_query_scope.py::test_collection_scoped_rows_only` ✓
- [x] G7.2 · §6 Step 7 · — · an explicitly requested out-of-scope `property_id` yields **404**, not an empty list · verify: `tests/integration/test_query_scope.py::test_out_of_scope_explicit_is_404` ✓
- [x] G7.3 · §6 Step 7 · — · owner/admin behaviour is **unchanged** · verify: `tests/integration/test_query_scope.py::test_privileged_unchanged` ✓ (+ `test_privileged_with_scope_rows_still_sees_everything` — A11 end to end)
- [x] G7.4 · §6 Step 7 · — · **one app-level dependency enforces all 142 declarations** (`enforce_declared_action`), rather than 142 hand-edits · verify: the whole web suite runs authenticated; an anonymous request is 401

> **Enforcement is one dependency, not 142 edits — and that is what makes G6's harness pay off.**
> Step 4's harness guarantees every route *carries* a declaration; `enforce_declared_action`
> guarantees every declaration is *consulted*. Neither alone answers N1's warning that "the edits
> are hopeful rather than verified"; together they do.
>
> **`require_action_gate` is deliberately not `require_permission`.** A `SCOPED` grant cannot be
> settled at dependency time because `target_property` is still `None` there — calling the full
> check would 403 every staff request to an item route, the exact unreachable-code failure N5
> names. So the gate rejects `DENY` outright and returns the grant; `SCOPED` is answered by the
> query layer, per §9.4 step 4.
>
> **The 404 for an out-of-scope `property_id` falls out of scoping `Property` itself.** No route
> code was touched: `resolve_identifier` finds nothing, raises `EntityNotFoundError`, and W.3's
> app-level handler turns it into a 404. An empty list would have confirmed the property exists.
>
> **`None` ≠ `frozenset()` in `current_property_scope`, and the distinction is load-bearing.**
> `None` is "unrestricted" (owner/admin, the CLI, background jobs); `frozenset()` is a staff
> member with zero scope rows who must see nothing (D3). A nullable set where "empty" doubled as
> "unset" is the single most likely way this layer fails open. Privileged roles bind `None`
> rather than "every property id" on purpose: a snapshot taken at request start would exclude a
> property created during the request, and that failure would read as a caching bug rather than
> an authorization one.
>
> **The scoped-model list is derived from `ENTITY_CLASSES`**, so N4 is enforceable rather than
> aspirational — a new property-scoped model is scoped the moment it is classified, and G1's
> `test_every_model_is_classified` refuses to let it go unclassified. `Property` is scoped by its
> own `id`; everything else by `property_id`.
>
> **`test_scoping_is_not_a_tasks_only_patch` is the test that proves the architecture.** The
> dashboard aggregates across properties and takes no `property_id` at all, so a per-route filter
> would have missed it entirely. It passes for free because the filtering is in the query layer —
> and it is what fails if someone later "simplifies" this into a per-route argument.
>
> **`conftest.web_client_factory` now signs in as an owner.** Before G7 the app enforced nothing,
> so an anonymous `TestClient` reached every route; ~500 web tests would otherwise have become
> 401 assertions. Owner is the right default — it keeps those tests testing their own subject
> instead of quietly becoming authorization tests — and role-specific coverage lives in
> `web_client_as`.

### [ ] G8 — Step 8: field-level redaction applied — *dep: G2, G7*
- [ ] G8.1 · §6 Step 8 · A12 · redaction applied in the **web serializer**; one test per `REDACTED_FIELDS` model — absent for staff, present for admin · verify: `tests/unit/test_redaction.py::test_money_hidden_per_model`
- [ ] G8.2 · §6 Step 8 · A13 · D12 — staff see `company_name`/`contact_name`/`phone`/`email`/`contacts` only; never `insurance_info`, `license_number`, `notes`, ratings; **no create/edit/delete** · verify: `tests/unit/test_redaction.py::test_vendor_contact_only`
- [ ] G8.3 · §6 Step 8 · A12 · **`Event.budget` and the `Insurance` columns** (C9) are covered by the census decision, not by omission · verify: `tests/unit/test_redaction.py::test_money_census_is_complete` stays green with zero allowlist entries lacking reasons

### [ ] G9 — Step 9: document visibility — *dep: G7*
- [ ] G9.1 · §6 Step 9 · A14 · migration `0004`: `documents.staff_visible` `Boolean`, `default False`, `nullable=False` (D13, fail closed) · verify: round-trip clean + autogenerate empty
- [ ] G9.2 · §6 Step 9 · A14 · staff queries filter on `staff_visible` **and** property scope resolved via `entity_type`/`entity_id` per **C11**; `entity_id IS NULL` → invisible to staff · verify: `tests/integration/test_documents.py::test_default_hidden`
- [ ] G9.3 · §6 Step 9 · — · owner/admin toggle in the UI · verify: `tests/integration/test_documents.py::test_toggle_requires_privilege`

### [ ] G10 — Step 10: AI scoping — **the highest-risk step; A15 is the phase's definition of done** — *dep: G2, G8*
- [ ] G10.1 · §6 Step 10 · — · thread a **required positional** scope through `assemble_context()` — signature per **C3**, not §4.3's literal line; N2: no default, no optional · verify: `tests/integration/test_ai_scoping.py::test_scope_is_required`
- [ ] G10.2 · §6 Step 10 · — · all **15** executors + `agent_stream()` take the scope set; enforced **at the query**, not in the prompt (§9.3) · verify: `tests/integration/test_ai_scoping.py::test_all_executors_take_scope` (parameterised over `_EXECUTORS.keys()`, **never a literal list** — §0.4)
- [ ] G10.3 · §6 Step 10 · A15 · **the exfiltration test**: two properties with distinguishable data; for **each** executor, staff scoped to A cannot obtain B's rows by any phrasing — "all", by B's name, **and by aggregate** (§9's adversarial pattern: an aggregate passes a row filter while still leaking a total) · verify: `tests/integration/test_ai_scoping.py::test_no_cross_property_exfiltration`
- [ ] G10.4 · §6 Step 10 · A16 · redaction holds **through the AI path**, not just the web serializer (N3 — never redact in templates) · verify: `tests/integration/test_ai_scoping.py::test_money_redacted_in_context`
- [ ] G10.5 · §0.5 · — · migrate `tests/integration/test_smoke_all_tools.py` to the new required signature (condition D) · verify: smoke green

### [ ] G11 — Step 11: onboarding flow — *dep: G3*
- [ ] G11.1 · §6 Step 11 · — · migration `0005`: `onboarding_state` (§4.2) · verify: round-trip clean + autogenerate empty
- [ ] G11.2 · §6 Step 11 · A17 · 6-step wizard; steps 2 (create account) + 3 (first property) the **only** hard requirements; prefill name from the Google profile, default type `household`, require only the property *name*; idempotent + resumable · verify: `tests/integration/test_onboarding.py::test_resumable`
- [ ] G11.3 · §6 Step 11 · A18 · steps 4–5 skippable, skipping is a first-class path landing on the dashboard; **billing never blocks onboarding** (`ONBOARDING:143`) · verify: `tests/integration/test_onboarding.py::test_skip_optional`

### [ ] G12 — Step 12: invites — *dep: G4, G11* — *pre-flight: confirm P4 `EmailService` before task 1*
- [ ] G12.1 · §6 Step 12 · A20 · `invite_service`: create/resend/revoke/accept; tokens **hashed** (D5 — only the hash is stored), single-use, **7-day** expiry (B9); §6.3 mismatch-notify · verify: `tests/integration/test_invites.py::test_token_lifecycle`
- [ ] G12.2 · §6 Step 12 · A21 · a staff invite with **zero** `property_ids` is rejected (D3, `ONBOARDING:164`) · verify: `tests/integration/test_invites.py::test_staff_needs_scope`
- [ ] G12.3 · §6 Step 12 · A19 · seat re-check **inside** the acceptance transaction; two concurrent acceptances at the cap → exactly one succeeds (`PRICING` §3.2 rule 5; D6 — seat = active memberships + pending invites) · verify: `tests/integration/test_invites.py::test_seat_race`
- [ ] G12.4 · §6 Step 12 · — · email types `welcome`, `staff_invite`, `invite_accepted` on the SPEC-001 `EmailService` · verify: `tests/integration/test_invites.py::test_emails_sent`

### [ ] G13 — Step 13: account switcher — *dep: G0* — *conventions §4.3: O1 blocks **Step 15**, not this step*
- [ ] G13.1 · §6 Step 13 · — · D11 — updates `sessions.current_account_id` **server-side**, persists `last_used_account`; switching changes every subsequent request's data · verify: `tests/integration/test_switcher.py::test_switch_changes_data`
- [ ] G13.2 · §6 Step 13 · A24 · the control is **absent** (not merely disabled) for single-membership users · verify: `tests/integration/test_switcher.py::test_hidden_when_single`

### [ ] G14 — Step 14: owner transfer + member offboarding — *dep: G3*
- [ ] G14.1 · §6 Step 14 · A22 · last-owner invariant — the last owner can be neither removed nor demoted · verify: `tests/integration/test_membership.py::test_last_owner_protected`
- [ ] G14.2 · §6 Step 14 · A23 · `transfer_ownership` against `memberships` + its partial unique index (SPEC-002 D4), **never** `accounts.owner_user_id` (B2 — the column does not exist); leaves exactly one active owner · verify: `tests/integration/test_membership.py::test_transfer_invariant`
- [ ] G14.3 · §6 Step 14 · — · on offboarding, tasks/notes/issues/uploads stay with the **account** (`ONBOARDING:225`) · verify: `tests/integration/test_membership.py::test_content_stays_with_account`

### [ ] G15 — Step 15: per-tenant config UI — *dep: G3* — **split by O1 (§0.7)**
- [ ] G15.1 · §6 Step 15 · A27 · settings form over the existing `config_service`; **owner/admin only**, staff 403 · verify: `tests/integration/test_settings.py::test_staff_denied`
- [ ] G15.2 · §6 Step 15 · A27 · secrets masked on read **everywhere** — web UI **and** `mihomes config list` (`cli/config.py:39-50` prints them raw today) · verify: `tests/integration/test_settings.py::test_secrets_masked`
- [ ] G15.3 · §6 Step 15 · — · **[BLOCKED on O1]** the secret **write** path. N11 authorises deferral; mark `[!]` on arrival, append `[BLOCKED]`, spend no attempts · verify: n/a — deferred by decision, recorded in U1

### [ ] G16 — Step 16: Telegram bot scoping — *dep: G2, G10*
- [ ] G16.1 · §6 Step 16 · A32 · migration `0006` + `models/telegram_link.py` per §4.2 — keyed on **`membership_id`** with `ondelete=CASCADE` (D19, N6 — never `Staff`); `UNIQUE(account_id, telegram_user_id)` · verify: `tests/integration/test_telegram_scope.py::test_revocation_cascades`
- [ ] G16.2 · §6 Step 16 · — · `/link <code>` flow — short-lived, single-use, **hashed** codes bound to `(user_id, account_id, membership_id)` · verify: `tests/integration/test_telegram_scope.py::test_link_flow`
- [ ] G16.3 · §6 Step 16 · A28 · resolve the sender from `message["sender"]` (`client.py:158`); **unlinked → staff-level, not denied** (D16, a deliberate departure from `TELEGRAM_PRD:158`) · verify: `tests/integration/test_telegram_scope.py::test_unlinked_is_staff`
- [ ] G16.4 · §6 Step 16 · A31 · scope **both** DB paths — `orchestrator.ask`/`assemble_context` **and** `review.py:120` `_build_estate_context`; missing either leaves a hole (F5) · verify: `tests/integration/test_telegram_scope.py::test_both_paths_scoped`
- [ ] G16.5 · §6 Step 16 · A29 · a staff sender's financial question is **refused** · verify: `tests/integration/test_telegram_scope.py::test_staff_financial_refused`
- [ ] G16.6 · §6 Step 16 · A30 · D17 — a financial answer is **never** posted into a staff-containing group; the bot offers a DM · verify: `tests/integration/test_telegram_scope.py::test_group_dm_offer`
- [ ] G16.7 · §6 Step 16 · — · `_resolve_reporter` (`responder.py:340-347`) prefers the **resolved sender** over the LLM's fuzzy `Staff.name ILIKE` guess · verify: `tests/integration/test_telegram_scope.py::test_reporter_from_sender`

### [ ] G17 — Step 17: cross-cutting adversarial leak matrix — *dep: all*
- [ ] G17.1 · §6 Step 17 · — · the leak matrix — for each entity class in §4.1 (as corrected by C10), assert staff reach is exactly what the classification says, across **web + AI + bot** · verify: `tests/integration/test_leak_matrix.py::test_staff_reach_matches_classification`

### [ ] G-Final — Compound-stop verification (conventions §4.1)
- [ ] F.1  · full-suite `pytest -q` green with the §0 env (condition C)
- [ ] F.2  · every §8 criterion green **by the test named in its own row**, run by node id with `-rs`, requiring `passed` — never a green suite (condition E)
- [ ] F.3a · walk §6 Steps 1–17 top-to-bottom: every step has a task (condition B, steps)
- [ ] F.3b · walk §8 A1–A33 top-to-bottom: every criterion has a gate (condition B, criteria)
- [ ] F.4  · `alembic upgrade head` → `downgrade` → `upgrade` clean; `alembic revision --autogenerate` **empty**; single head
- [ ] F.5  · write `tasks/build-loop-spec003-report.md` (conventions §5)

---

## 2. Group-specific gates (conventions §2)

> *"Add a custom gate when a group's failure mode is (a) damage to state rather than code, so a
> passing test does not prove safety, and (b) load-bearing for a later group."*

Four groups qualify. **The failure mode here is not corruption — it is a silent allow**, which a
passing test proves nothing about.

| Group | Gate | Failure class |
|---|---|---|
| **G2** | **G-census** + **G-exists** (§0.4) | a redaction dict that redacts nothing, silently |
| **G5/G6** | shrinking allowlist is monotonic and reaches **0**; scratch-route probe fails the suite | a harness with no teeth |
| **G10** | executors enumerated from `_EXECUTORS` at test time; the aggregate arm, not just the row arm | the leak that looks like the feature working |
| **G3, G9, G11, G16** | migration round-trip (`upgrade`→`downgrade`→`upgrade`) + `autogenerate` empty | reversibility, convergence |

**Mutation-test the security gates before believing them.** SPEC-002's G17 found *two of four
arms had no teeth* and its G12 verified 8 controls by mutation. For G10 in particular: break the
scope filter deliberately and confirm A15 goes **red**. A security test that cannot fail is
conventions §0's *"gate that cannot fail is not a gate"*, and this phase is made of them.

---

## 2.1 RUN STATE — where a resuming session picks up

**Landed:** G0–G7. Suite: **1676 passed, 3 skipped, 2 xfailed, 0 failed** (1562 baseline → 1676,
+114 tests).

**Resume at G8.1** — apply `redact_for_role` in the web serializer (the function and its census
gates landed at G2; nothing calls it yet).

**RBAC is now live for roles and rows, not yet for fields.** As of G7 an anonymous request is
401, every one of the 142 declared routes goes through the capability matrix, and staff queries
are constrained to their whitelist at the query layer. **Still not enforced: field-level
redaction** — `redact_for_role` exists, is mutation-verified, and **no serializer or AI path
calls it**, so a scoped staff member currently sees the *costs* on rows they are legitimately
allowed to see. That is G8, and it is F4's whole point.

**A15 — the phase's definition of done — is not green.** G10 has not started. The spec's own
words: *"Roles enforced in the UI while the AI answers freely is not a partial success — it is
the leak wearing the feature's clothes."*

**Poison ceiling: 0 of 5 used.** `G15.3` will be `[!]` **by decision** (O1/N11) and does not
count.

---

## 3. Circuit breaker (conventions §3)

Halt and write the report with status `HALTED` if: **>5** tasks poison; **G2, G3, G5, or G10**
poisons (each is load-bearing for everything after it — G10 is the definition of done); or **two
consecutive groups** fail their full-suite gate.

`G15.3` is `[!]` **by decision, not by failure** (§0.7) and does **not** count toward the ceiling.
