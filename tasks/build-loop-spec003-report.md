# SPEC-003 Build Loop — End-of-Run Report

> **Generated:** 2026-08-20 · **Branch:** `worktree-spec-build-harness` → `origin/spec-build`
> **Baseline:** `c09c54d` (SPEC-002 complete) · **Head:** `becd5cf`
> **Spec:** `docs/specs/SPEC-003-phase2-onboarding-team-rbac.md`
> **Invocation:** `/loop tasks/build-loop-spec003.md`, driven group-by-group across several sessions

---

## Status: ✅ COMPLETE

All five compound-stop conditions hold (conventions §0):

| Cond | Requirement | Result |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | ✅ G0–G17 + G13.5 + G-Final all `[x]`; **zero unchecked boxes**, verified programmatically rather than by eye. **Zero poisoned tasks** — poison ceiling 0 of 5 used. `G15.3` was pencilled in as `[!]` blocked on O1 and turned out to be closable *by refusal* (N11 specifies the behaviour), so it is `[x]`. **G-Final found six stale boxes**: G4 and G6 had ticked-adjacent work with unflipped sub-items, and G11.4 was still `[ ]` after G13.5b discharged it. Each was verified against the code before ticking, not ticked because the group looked done — G4.4's row named a test that does not exist, and its actual coverage turned out to be two tests with a documented deviation. |
| **B** | every §6 step tasked **and** every §8 criterion gated | ✅ F.3a: **17/17** steps, one task each · F.3b: **33/33** criteria (A1–A33), none ungated |
| **C** | full suite green | ✅ **1852 passed**, 0 failed, 0 errors, 3 skipped (declared), 2 xfailed (declared) |
| **D** | smoke green | ➖ No dedicated smoke file for this spec; F.1's full-suite gate is the structural equivalent, per SPEC-001/002 precedent |
| **E** | every §8 criterion green **by its own named test** | ✅ **50 passed, 0 skipped** — 35 node ids expanding to 50 tests via parametrization, run explicitly by node id with `-rs`. Zero skips: condition E requires `passed`, not "did not fail". |

**F.4 — migration integrity:** `base → head → base → head` clean on a scratch database; single
head (`0007_telegram_links`); `alembic check` → *"No new upgrade operations detected."*

**Two node ids in §8's table do not exist under the names the spec gives them**, both documented
renames rather than gaps: A12's test is `test_money_hidden_for_staff` (not
`test_money_hidden_per_model`), and A19's `test_seat_race` was **deliberately replaced** by
`test_cap_is_enforced_under_the_lock` + `test_acceptance_serialises_on_the_account_row` after the
racing version hung the suite twice (see *Lessons*). Corrected in this run's F.2 list.

**Also worth stating:** §8's node ids are written as `file::name`, but most of these tests live in
classes, so the bare form resolves to nothing — `pytest` reports `ERROR: not found` and then
`no tests ran`, **exit code 0-adjacent noise that a careless F.2 would read as green**. The list
was resolved to real `file::Class::name` ids by AST walk before running.


**One repeat finding, now twice in a row.** SPEC-002's G-Final found three groups with headers
marked done and sub-item checkboxes never flipped; SPEC-003's found six such boxes across three
groups. A group's own gate proves its *tests* pass; nothing in the per-group loop re-reads its
checkboxes, so the DAG drifts from the code in the one direction that looks like progress. The
fix is not more diligence — it is that **G-Final must walk every box programmatically and verify
each against the code before ticking it**, which is what caught G4.4's nonexistent test name.
Added to `lessons.md`.

---

## Per-group summary

| Group | What | Commit | Tests | Suite after |
|---|---|---|---|---|
| pre-flight | Harness + 18 corrections (C1–C18); the spec's premise stale in 13 places | `3b53bae` | — | 1562 |
| G0 | Bind the tenant to a web request — **the prerequisite §6 never lists** (C12) | `4af3090` | 3 | 1565 |
| G1 | The capability matrix as data — 21 keys/20 rows, R1/R2, 44 models classified | `5425dba` | 18 | 1583 |
| G2 | Scope primitive + redaction — **the census caught §4.4's own leak** (C8: six named fields did not exist) | `cee00a3` | 28 | 1611 |
| G3 | `require_permission` + audit — deny-audit survives the rollback | `03d8ddd` | 48 | 1659 |
| G4 | Entitlements — `can()`/`usage()`, two tables because D18 flips nothing yet | `4fdf211` | 9 | 1668 |
| G5 | The fail-closed route harness — **built before the 142 edits**, per N1 | `9453798` | — | 1668 |
| G6 | 142 route declarations across 23 files — and the four the vocabulary cannot name | `4956b81` | — | 1668 |
| G7 | Enforcement goes live — **one dependency, not 142 edits** | `eaedda7` | 8 | 1676 |
| G8 | Redaction *applied* — the unit tests had passed for two groups while nothing called it | `478b92e` | 5 | 1681 |
| G9 | `documents.staff_visible` — fail closed at the schema, not the constructor | `c7a95b5` | 7 | 1688 |
| G10 | **AI scoping — A15, the definition of done. Caught two live leaks.** | `b40bbea` | 23 | 1711 |
| G11 | Onboarding — and a migration bug only a rebuilt database could show | `5b99c70` | 15 | 1726 |
| G12 | Invites — A19 rewritten after the racing version hung twice | `9d841af` | 22 | 1748 |
| G13 | Account switcher — reading across the tenant boundary, deliberately | `cf3b3bf` | 9 | 1757 |
| G13.5 | **`Access.SESSION`** — the route class §4.1 does not have, and needs | `9f0401a` | 6 | 1763 |
| G14 | Owner transfer — A22's demotion arm is enforced by R1, not the check | `9f00f31` | 16 | 1779 |
| G15 | Config UI — O1's write path closed **by refusal**, not deferral | `75d7d1a` | 24 | 1803 |
| G16 | Telegram bot scoping — the bot binds a role on **every** path, unlinked included | `f5ed482` | 23 | 1830 |
| G17 | **The leak matrix — two more leaks, found by asking which classes enforce anything** | `becd5cf` | 22 | **1852** |

**Suite delta:** **1562 → 1852**, **+290 tests**, 0 failures throughout.

*Every suite-after figure above is the number stated in that group's own commit body, not a
reconstruction — the first draft of this table interpolated them and was wrong in fourteen of
sixteen rows, which is exactly the kind of plausible-looking number a report should not carry.
The `Tests` column is each group's own count of tests added, which is why it does not always
match the suite delta: parametrized families expand at collection time.*

---

## Criteria reconciliation — all 33 green

| # | Criterion | Status |
|---|---|---|
| A1–A3 | Matrix rows, R1, R2 | ✅ `test_matrix.py` |
| A4–A5 | Every route declares; allowlist only shrinks | ✅ reached **0** allowlist entries |
| A6–A9 | Item/collection route classes, 404-not-403, immediate revocation | ✅ `test_permissions.py` |
| A10–A11 | Zero scope = zero properties; privileged ignore scope rows | ✅ `test_scope.py` |
| A12–A13 | Money redacted per model; vendor contact-only | ✅ `test_redaction.py` (A12 renamed) |
| A14 | New document invisible until `staff_visible` | ✅ `test_documents.py` |
| **A15** | **AI exfiltration — the definition of done** | ✅ **all 15 executors** |
| A16 | Redaction holds through the AI path | ✅ `test_ai_scoping.py` |
| A17–A18 | Onboarding resumability; skip optional | ✅ `test_onboarding.py` |
| A19–A21 | Seat cap under lock; token lifecycle; staff needs scope | ✅ A19 rewritten, see *Lessons* |
| A22–A23 | Last owner protected; transfer invariant | ✅ `test_membership.py` |
| A24 | Switcher hidden for single-account users | ✅ `test_switcher.py` |
| A25–A26 | `can()` names an upgrade target; two independent gates | ✅ `test_entitlements.py` |
| A27 | Staff 403 on config UI; secrets masked everywhere | ✅ two tests |
| A28–A32 | Bot: unlinked-is-staff, refusals, both paths, cascade | ✅ `test_telegram_scope.py` |
| A33 | Every privileged action **and every deny** audited with a real actor | ✅ `test_audit.py` |

---

## Bugs found — four live leaks, all closed

The number that matters. None of these were introduced by this phase; all four were **live in
`main`** and are the reason the spec calls A15 the definition of done.

**1. The assistant returned the household's finances to staff** (G10). `query_budget` and its
siblings took no scope, so a housekeeper scoped to one property could ask the AI for the account's
budget and be answered. §4.1's row 9 denies staff finances; the web surface honoured it and the AI
did not.

**2. The AI rendered money straight from the ORM** (G10). Even where rows were correctly scoped,
amounts reached the model unredacted — F3's exact shape, and the reason N3 forbids redacting in
templates: the AI path renders no templates.

**3. `/library/` returned another property's books to scoped staff** (G17). `Book` was classified
`ACCOUNT_LEVEL` — which enforces *nothing* — behind a route declaring `inventory.manage`, which
row 7 grants staff as `SCOPED`. Fixed by reclassifying to `PROPERTY_SCOPED`, which is what
`Book.property_id` and the route declaration already agreed on.

**4. `/ai/sessions/{id}` returned an owner's saved AI answer to scoped staff** (G17). **The leak
G10 structurally could not see.** G10 scoped the *live* path and proved a staff member asking
about another property gets nothing. The transcript of a question an *owner already asked* is a
stored row on a different route, and `AIConversation` carries no author column at all (`role` is
the AI persona, not the member). Two surfaces onto the same data, one of them scoped.

**Plus one fail-open discovered in the mechanism itself** (G10): the property-scope listener was
not armed on every session factory, so some paths applied no filter at all.

### Bugs found and *not* fixed (scope-respecting, all in `opportunities.md`)

- **`/ai/` and `/ai/sessions-panel` return HTTP 500 for every role, owners included.**
  `func.min(AIConversation.id)` — Postgres has no `min(uuid)`. A SPEC-002 G6.1 casualty, dead
  since that conversion, not an RBAC defect. Pinned by `TestKnownBrokenCells` so the leak matrix
  cannot book a 500 as a denial.
- **Staff can *write* money they cannot read** — `complete_work_order` sets `actual_cost` under
  `issue.manage`. D14 covers reads; declaring `finance.view` would deny staff the ability to
  complete work, which D14 explicitly rejects.
- **Five property-scoped child tables are reachable by out-of-scope staff** — proven, not assumed.
  `Guest` is the sharpest: a name is not money, so no redaction covers it.
- The `membership.py` `__table_args__` bug; N2's `UNSET` third state; four approximate action
  declarations (now three — G17 resolved the library one by finding it was a leak).

---

## New lessons and pointers

Seventeen entries added to `tasks/lessons.md` this run; the four that changed how the rest of the
phase was built:

- **A test that hangs is waiting on a lock, not looping.** Two attempts at A19's concurrent-
  acceptance race stalled the suite for five minutes each. The threads were fine: the `session`
  fixture holds an open transaction, and inserting any tenant row makes Postgres take
  `FOR KEY SHARE` on the referenced `accounts` row — which conflicts with the `SELECT … FOR UPDATE`
  the code under test takes. `pg_stat_activity` names the holder in one query, and "idle in
  transaction" is the fixture.
- **Prefer testing the mechanism over staging the race.** The thread-and-barrier version was not
  just slow to debug, it was the weaker assertion — a race test **fails to fail** whenever the
  first thread finishes before the second starts, so a broken implementation passes on a fast
  machine and becomes flaky in CI, where it gets retried rather than read. Asserting the two
  properties that *make* the race safe (the lock is exclusive; the check under it refuses) is
  deterministic, runs in two seconds, and additionally pins **which row** is locked.
- **A migration may read application code, never application state that later migrations change.**
  Three instances this phase, `0001_pg_baseline` being the third: it built drift-guard triggers
  from live `Base.metadata`, so adding a table in `0007` made the *baseline* try to create a
  trigger on a table that would not exist for six more revisions. A migration is a fixed point in
  history.
- **One gate at a time.** 788 `DeadlockDetected` errors came from two full suites running
  concurrently — and no source edits between launching a gate and reading its result, or the
  result describes a tree that no longer exists.

`tasks/opportunities.md` gained **33 entries** across this phase's sections: 14 `[BUG]`,
5 `[DEFER]`, 11 `[OPT]`, 3 `[PATTERN]`, 0 `[BLOCKED]`. The three patterns are the ones worth
reading before SPEC-004 — the migration-state rule above, the derived-vs-transcribed gate
observation, and U7's classification-without-a-mechanism finding. **Zero `[BLOCKED]` is the
number to notice:** nothing in this phase was abandoned unresolved, and the one task pencilled in
as blocked (`G15.3`, on O1) turned out to be closable by refusal rather than deferral.

---

## Unmet launch gates

| # | What | Owner |
|---|---|---|
| **U1** | **O1** — provider API keys stay plaintext in `configurations.value`. Step 15 masks on display; the UI says so, because §10 is explicit that masking does not make them safe. | founder |
| **U2** | Mis-declared actions — **now partly mechanical.** G17's static check closes the sub-case where a mis-declaration contradicts the entity classification, which is the shape both real leaks took. Residual: a mis-declaration no classification contradicts. | human review |
| **U3** | Aggregate inference — A15 tests direct paths, not inference. Accepted. | accepted |
| **U4** | Bot transport — Step 16 scopes *answers*; the bot still polls with a token in per-account config (N7). | Phase 4+ |
| **U5** | Inherited from SPEC-002: S1 archival, S7 demo mode, S5 polymorphic drift is app-only. | founder / accepted |
| **U6** | **No entity class fits `Template`/`TemplateItem`**, and `PERSONNEL`'s "own record only" rule has no matrix key. Both pinned with reasons and asserted, so the entries cannot outlive the gap. | spec work |
| **U7** | **Three of six entity classes are enforced by nothing**; two more are reached by model *name*, so a newly-added member inherits nothing. Both G17 leaks were of this shape. | Phase 3 |

---

## Verification evidence

```
F.1  full suite    1852 passed, 3 skipped, 2 xfailed, 4 warnings in 257.50s
F.2  criteria      50 passed, 2 warnings in 10.75s        (35 node ids, 0 skipped)
F.3a steps         17/17 §6 steps have a task
F.3b criteria      33/33 §8 criteria referenced and gated
F.4  migrations    base → head → base → head clean; single head 0007_telegram_links
                   alembic check: "No new upgrade operations detected."
```

**Security gates were mutation-tested, not trusted.** G1 (4 mutations), G10 (the scope filter
broken deliberately, A15 confirmed red), G17 (3 mutations — reverting `Book`'s class, reverting the
transcript declaration, dropping `Asset` from `scoped_models()`; the second failed in the runtime
probe *and* the static scan independently). Conventions §0: a gate that cannot fail is not a gate,
and this phase is made of them.

---

## The three things worth carrying forward

**1. Enforce where a forgetting caller cannot bypass it.** Two of the four leaks existed because a
control lived at one call site instead of one seam. Enforcement went live in G7 as **one FastAPI
dependency** rather than 142 route edits; scoping filters at the **query layer** rather than
post-hoc; redaction lives in **one function** both surfaces call. Each choice makes the leak
unreachable rather than merely absent — and each was cheaper than the alternative.

**2. A classification is not a control.** `EntityClass` had six values; exactly **one** was read
by any code. `ACCOUNT_LEVEL` — the strictest-*sounding*, `✗ for staff` — was enforced by nothing,
and **both** G17 leaks were `ACCOUNT_LEVEL` models behind staff-granted routes. The grep that
found this cost one command and reordered the entire group.

**3. Derive gates; never transcribe them.** Every gate that found something built its expectation
from the schema or the router table rather than a written list: G2's money census caught six
redaction fields that did not exist, G5's harness reached zero allowlist entries, G11's registry
refused a wrong classification, G16's two index/id gates refused a new model, and G17's derived
census **turned red on its own author** — naming two models missing from the first draft of my own
exception dict. A hand-written matrix would have been green and wrong.
