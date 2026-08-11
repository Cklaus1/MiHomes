# Spec Build Loop — Shared Conventions

> **Input specs:** `docs/specs/SPEC-001` … `SPEC-008` — the source of truth for *what* to build.
> **This file:** the mechanisms shared by every per-spec harness — *how* to execute a spec
> autonomously to completion, resumably, without intermediate human review.
> **Per-spec harnesses:** `tasks/build-loop-spec001.md`, `…spec002.md`, … Each names its own
> groups, gates, and prerequisites; all of them inherit this file.
> **Invocation:** `/loop tasks/build-loop-spec00N.md`

Derived from `tasks/build-loop.md` (Chris's hardening harness, `be8d398`), which ran once to
completion. Where this file departs from it, the reason is stated — the departures matter more
than the similarities, because that harness was **corrective** (fixing covered code) and this
one is **constructive** (building code that does not exist).

---

## 0. Prime directive & compound stop condition

Work the per-spec DAG until the **compound stop condition** holds. Five conditions, all required:

| | Condition | Proven by |
|---|---|---|
| **A** | Every checkbox in the harness §1 is `[x]` or `[!]` | the DAG itself |
| **B** | Every §6 step has a task **and** every §8 criterion has a gate | **F.3a + F.3b** |
| **C** | Full test suite green (*per-spec definition — see §0.1*) | `pytest -q` |
| **D** | Smoke green (*per-spec definition — see §0.1*) | the named smoke file |
| **E** | Every §8 criterion green **by the test named in its own row** | that test |

**A skipped test is a red gate.** Not pedantry — it is the most likely way this harness reports
a false success. SPEC-001 §9 and SPEC-002 §9 both specify their Postgres fixture as *"skipping
when unset"*, and a skip does not fail a run: `pytest -q` exits 0, and the baseline already
carries one (`test_watchdog.py:20`, POSIX-only). So if `TEST_DATABASE_URL` is missing from the
**loop's own** environment, every Postgres-dependent criterion silently skips, the suite reads
green, and C passes while the criteria that prove the database works never executed. A gate
that cannot fail is not a gate. Where a criterion depends on an environment-gated fixture, gate
on that test **reporting `passed`** — run it by node id with `-rs` and require `1 passed` —
never on the suite merely being green.

**All five. No subset terminates the loop.** A green suite with unchecked tasks is not done. A
full DAG with a red suite is not done.

**B and E are not redundant.** E proves each criterion's test *passes*. B proves no criterion was
**omitted from the DAG in the first place** — a criterion that appears in no group's gate would
satisfy E vacuously, because there is no test to run. Chris's equivalent condition caught four
findings that had never been assigned to any group. This is why F.3 is split in two (§4.1).

**Condition E is new, and it is the answer to greenfield.** Chris's C and D are real gates when
*fixing* code the suite already covers. They are not sufficient here: a **stub can satisfy
A+B+C+D**, because the suite never touched the new code and the smoke path never reaches it.
E binds completion to the spec's own §8 table, which every spec states in its own words —
SPEC-001 §8: *"A criterion without a test does not count as met."*

**No intermediate review stops.** Do not pause for approval between tasks or groups. The specs
are reviewed and their locked decisions are canon (each spec's §1.1). A genuinely new blocking
question goes to `opportunities.md` and the task is deferred — the loop continues with the next
unblocked task.

### 0.1 C and D are per-spec, never inherited

Each harness must state what C and D mean for its spec, because the suite itself is a build
target in this set:

- **SPEC-001** — C is `pytest -q` green. There is no D: Phase 0 ships a separate landing app,
  and no smoke file covers it. A harness with no D says so explicitly rather than silently
  dropping a stop condition.
- **SPEC-002** — Step 15 *replaces* `conftest.py`'s in-memory SQLite engine with a Postgres
  fixture, and both smoke files (`tests/integration/test_smoke_all_tools.py`,
  `test_web_smoke.py`) are SQLite-bound — they are themselves migration targets. So C is
  "the **migrated** suite green on Postgres" and D is "the **migrated** smoke green." Inheriting
  Chris's wording here would gate on a suite that cannot run.
- **SPEC-003 onward** — C is "suite green **including this spec's new tests**."

---

## 1. The loop hierarchy

Five nested loops. Inner is per-task; each outer layer widens the blast-radius check.

### 1.1 Inner loop — one task to green (max 3 attempts = poison ceiling)

1. **Write the failing test first.** The test named in the step's `verify:` clause, or in the
   §8 row it discharges. It must **fail before the change and pass after** — run it and see it
   red. In corrective work test-first is a discipline; here it is the **only thing standing
   between a stub and a green gate**, so it is a gate, not a principle. A test that passes
   before the implementation exists is testing nothing.
2. **Implement the step** exactly as the spec's §4 (schemas), §5 (signatures), and §6 (step
   body) specify. The specs give real SQLAlchemy source and real Python signatures — use them
   verbatim; do not paraphrase a schema.
3. **Run the task's test** + any test named in its `verify:` line. Red → 4. Green → 5.
4. **Diagnose & retry.** Increment the attempt counter. On attempt 3 failing → **poison**:
   revert the task's changes (`git checkout -- <touched>`), mark `[!]` in the DAG, append a
   `[BLOCKED]` line to `opportunities.md` with the failure and what would unblock it, move on.
   Never let one task stall the run.
5. **Regression gate (§1.2).**

### 1.2 Outer loop — no collateral damage

After the task is individually green, run the **affected-area suite** — the test modules for the
step's area (a model step runs `tests/unit/`, a route step runs `tests/web/` +
`tests/integration/`). Any *new* red that was green at the group's start means this step broke
something → treat as an inner-loop failure (back to 1.1.4). Consult the spec's **§7 non-goals**
first: most wrong turns in this set are pre-named there.

### 1.3 Meta loop — per-group commit + checkbox (resumability boundary)

When every task in a group is `[x]` or `[!]`:

1. Run the **full suite** (§1.4).
2. Green → **commit** with `spec00N(G#): <group title> — <N steps, M tests added>` and tick the
   group's header checkbox **and** its mirror in `todo.md`.
3. **The commit is the resume point.** On restart, read the DAG: the last group whose header is
   `[x]` and whose commit exists is done; resume at the first `[ ]` task of the first incomplete
   group. **Checkboxes + commits are the only state** — no external run log, no memory of the
   prior process.

**One step per group, by default.** Every spec's §6 states its steps are *"independently
verifiable and separately committable."* Take that literally: the group boundary is the commit
and the resume point, so grouping two steps means a poisoned second step reverts with the
first's work uncommitted. Combine steps into one group only when the spec itself says they must
land together.

### 1.4 Full-suite regression loop

Before every group commit and once at the end: run the **entire** suite (`pytest -q`). Distinct
from §1.2 — a model change can break a web test three groups away. Full green is a precondition
for the group commit and for the compound stop.

### 1.5 Smoke loop

Where the spec has a smoke target (§0.1), it runs as part of every full-suite pass once its
group lands. Where it has none, the harness says so and drops condition D explicitly.

---

## 2. Gate patterns — when a group needs more than red/green

Chris's R4 group carried four extra gates because migrations corrupt data irreversibly and a
later group rode on them. The generalizable rule:

> **Add a custom gate when a group's failure mode is (a) damage to *state* rather than code, so
> a passing test does not prove safety, and (b) load-bearing for a later group.**

Each gate should target a *different failure class*: reversibility, intra-operation ordering,
value preservation across a transform, and convergence (run the generator again and get nothing).

**Chris's four R4 gates do not port to this set.** SPEC-002 Step 6 *squashes* main's 40 Alembic
revisions into one `0001_pg_baseline` and archives the old ones to `alembic/legacy_sqlite/`.
Against an empty Postgres, a data-preservation gate is meaningless. What actually gates
migration work here:

| Gate | Check | Failure class |
|---|---|---|
| **round-trip** | `alembic upgrade head` → `downgrade` → `upgrade`, clean, on empty Postgres | reversibility |
| **autogenerate-clean** | `alembic revision --autogenerate` produces an **empty** migration | convergence (models == schema) |

**Data preservation moves to the importer** (SPEC-002 Step 16), which carries its own ordering
invariant and therefore its own gate: upload and verify **all** objects *before* committing the
DB transaction, so a failure leaves *"orphaned objects (garbage), never dangling references
(corruption)."* The reverse order is prohibited. Gate: a simulated mid-import failure leaves no
partial account.

---

## 3. Poison ceiling & run-level circuit breaker

- **Per-task ceiling:** 3 attempts (§1.1.4), then `[!]` + defer to `opportunities.md`.
- **Run-level circuit breaker — halt and write the report with status `HALTED` if any of:**
  - more than **5** tasks end poisoned, or
  - a **prerequisite** (§3.2) or a step the spec names as load-bearing poisons, or
  - **two consecutive groups** fail their full-suite gate.

  A cascade means the spec's assumptions are wrong somewhere upstream; pushing further just makes
  a mess. **Halting is not failure** — it is the harness refusing to thrash.
- A poisoned task does not block its group's commit (the group commits with `[!]` noted).

### 3.1 Pre-flight re-verification — runs before task 1, halts on mismatch

**The specs' factual claims about the tree are ref-dependent, and the refs disagree.** SPEC-001
through SPEC-005 were written against `telegram-bot`; only SPEC-006 and SPEC-008 carry
`**Verified against:** origin/main @ be8d398`. Measured:

| Claim | Source | On `telegram-bot` | On `origin/main` | After SPEC-001 |
|---|---|---|---|---|
| "the existing 33 test files pass" | SPEC-002 §9, Step 15 | 33 ✓ | 82 | **95** |
| "28 of 33 use the `session` fixture" | SPEC-002 §9 | 28 ✓ | — | **43 of 95** |
| "the 780+ existing tests depend on it" | SPEC-001 §9 | ~780 ✓ | **1080** | 1233 |
| "36 domain tables" | SPEC-002 Steps 2–5 | 36 ✓ | 36 | **37** tenant-owned |
| "36 revisions" archived | SPEC-002 D9, §3 | — | **40** | 40 |

A harness targeting main could mark SPEC-002 Step 15 done **with 62 other test files broken**.
That is the stub-passes-the-gate failure mode, introduced by the spec itself.

**The gate compounds across specs.** These counts drift every time a spec lands, so each run
re-measures rather than trusting the previous run's numbers: SPEC-001 added 13 test files, which
alone moved Step 15's scope from 82 to 95. Re-verify against **HEAD**, not against `origin/main`.

**So before any step of a spec runs:** re-check that spec's counts, file paths, and line-number
citations against the target ref. On mismatch, **halt** and record the corrected value — do not
proceed on a false premise, and do not silently "fix" the number. `docs/specs/README.md` already
requires this: *"A claim about 'the code' without a ref is not a verified claim"* — and records
that skipping it cost real rework twice while SPEC-006 was written.

### 3.2 Prerequisites halt before task 1, they do not poison task-by-task

Several specs need infrastructure a loop cannot create — a reachable Postgres, a provisioned Fly
app, DNS, a Resend domain. SPEC-006 already models the pattern with an explicit *"Prerequisites —
not steps"* block. Each harness opens with a §0 prerequisites list; a missing prerequisite halts
**before** the first task with the missing list named, rather than poisoning three tasks in a row
on the same absent dependency.

### 3.3 Open decisions: poison only what blocks the build

Twelve `O`-labels are open across the set. **None of them block code** — every one is scoped to
launch configuration, content, or legal sign-off. SPEC-001 §1.3 states the pattern: *"None of
these block **writing code**. Each blocks **launching**."* SPEC-004's O1 is ~20 placeholder
prices, and the spec notes every step *"targets config keys and `STRIPE_PRICE_*` env vars, never
literals, so the code is complete and testable before the numbers exist."*

So classify each `O` as **blocks-build** or **blocks-ship**. Poison only on blocks-build. Carry
blocks-ship forward into the end-of-run report as an unmet launch gate — visible, not silently
satisfied.

**`O`-labels are per-spec-local.** Six unrelated `O1`s exist. Always resolve an `O` inside the
spec that raised it. Three inbound gates (SPEC-001 O1, SPEC-003 O1, SPEC-004 O1) are deliberately
carried in SPEC-005 §1.6 under their **original** labels — do not renumber them.

**One genuine blocker, and it is human:** SPEC-006's P2 — reconcile `telegram-bot` with
`origin/main` — *"Nobody owns this, and nothing below compiles on a tree without
`review_common.py`."* No autonomous loop resolves branch topology. SPEC-006 stays unharnessed
until that is owned.

---

## 4. Task DAG format

Each harness groups its spec's §6 steps, one step per group by default (§1.3), in the order the
spec states. Group headers carry the resume checkbox and their dependency as an italic trailing
clause. Task lines:

```
- [ ] G3.1 · §6 Step 3 · A8,A9 · email Protocol + ConsoleProvider + ResendProvider + render_template
      · verify: tests/unit/test_email_render.py::test_waitlist_confirmation_has_both_parts
```

Five `·`-separated fields: **checkbox + ID · spec-ref · criteria discharged · imperative one-liner
· `verify:` target**. States: `[ ]` todo, `[x]` done, `[!]` poisoned.

The `verify:` target is what makes `[x]` falsifiable — it must name a concrete test path, down to
`::test_name` where the spec does. The criteria field is what F.3b reconciles against §8.

### 4.1 G-Final — the stop condition, made executable

Every harness ends with a group whose tasks are the stop conditions themselves:

```
- [ ] F.1  · full-suite `pytest -q` green (condition C)
- [ ] F.2  · every §8 criterion green by its own named test (condition E)
- [ ] F.3a · walk §6 top-to-bottom: every step has a task (condition B, steps)
- [ ] F.3b · walk §8 top-to-bottom: every criterion has a gate (condition B, criteria)
- [ ] F.4  · write end-of-run report (§5)
```

**F.3a and F.3b are the highest-value tasks in the file.** Chris's single F.3 caught four spec
findings the DAG author had never assigned to any group; without it, condition B would have
passed on an incomplete DAG. Splitting it closes the vacuous-E hole: F.3a catches a dropped
step, F.3b catches a dropped criterion.

### 4.2 Parsing rules

- **`A`-labels are strings, not integers.** SPEC-005 has `A14b` and `A29b` — 36 rows with a max
  label of `A34`. Never range-check or count by label number.
- **The specs carry no checkboxes.** Steps are bold-prefixed headings (`**Step 7 — …**`), not
  `- [ ]`. The harness synthesizes its own completion state; the specs have no mutable progress
  field and must not be edited to add one.
- **Never mine §10 for work.** Specs 003–008 carry a §10 *"What this phase does not make safe"*
  listing **deliberate** residuals. SPEC-005 carries forward SPEC-004 §10's nine. These are
  decisions, not bugs.
- **SPEC-007 does not exist** — it is an unlinked placeholder in the index (Twilio; A2P 10DLC has
  regulatory lead time and no owner). Skip it by design; do not error on the gap.

### 4.3 Known stale cross-references — corrected at authoring time

Three §1.3 pointers are wrong in the specs. Trust §6, not §1.3:

| Spec | §1.3 says | Actual target |
|---|---|---|
| SPEC-003 | O1 blocks Step 13 | **Step 15** (Step 13 is the account switcher) |
| SPEC-006 | O1 is Step 8's WhatsApp half | **Step 7** (Step 8 is `notify_staff` fallback) |
| SPEC-006 | exit check says "Steps 0–11" | §6 defines **Steps 1–10** + two lettered prerequisites |

---

## 5. Three-artifact insight discipline

Every non-trivial insight lands in **exactly one** of three places — nothing lost, nothing in the
wrong file:

1. **`tasks/lessons.md`** — a *correction to how I work*: a mistake pattern plus the rule that
   prevents it. Dated section, existing format.
2. **`tasks/opportunities.md`** — *deferred work*: optimizations (one line, **not** acted on),
   bugs found outside the DAG (candidate tasks with proposed severity), blocked/poisoned tasks,
   and unmet blocks-ship gates. Curated input to the **next** loop, never acted on in this one.
3. **End-of-run report** (`tasks/build-loop-spec00N-report.md`) — *what this run did*. Written
   once, at STOP or HALT, as task F.4.

Line formats, carried as HTML comments in `opportunities.md` so appends stay well-formed:

```
- [OPT] file:line — one-line description (surfaced during <task-id>)
- [BUG][proposed-severity] file:line — title — concrete failure — proposed fix (surfaced during <task-id>)
- [BLOCKED] <task-id> — why blocked, what would unblock it
- [DEFER][<label>] file/area — what was deferred and why it is safe to defer
```

### End-of-run report contents

- **Status:** COMPLETE | HALTED (reason) — and which of A/B/C/D/E held.
- **Per-group:** commit sha, tasks done/poisoned, tests added, suite delta (before→after).
- **Poisoned tasks:** id, spec-ref, why, what would unblock (cross-ref `opportunities.md`).
- **Criteria reconciliation table:** every §8 `A`-label → {green + test | deferred + why}.
  Conditions B and E are provable from this table.
- **Unmet launch gates:** every blocks-ship `O` still open (§3.3).
- **New bugs / lessons:** counts + pointers.
- **Verification evidence:** the final `pytest -q` summary line, and each gate's raw output.

---

## 6. Non-negotiables

- **Test-first, always.** No step without a test that **failed before it and passes after**.
  See §1.1.1 — in greenfield work this is the load-bearing gate, not a style preference.
- **Minimal impact.** Only touch what the step names. New-scope bugs go to `opportunities.md`,
  never silent side-fixes.
- **Never mark done unfulfilled.** `[x]` requires a green test **and** a green affected-area
  suite. A group `[x]` requires full green **and** a commit.
- **Resumable by construction.** Commits + checkboxes are the only state. Any restart reads the
  DAG and continues.
- **Build the spec, not your idea of the spec.** §4 gives real SQLAlchemy source and §5 real
  signatures precisely so call sites are unambiguous. A prose paraphrase of a schema is a
  divergence.
- **"Locked" means decided, not built.** The set-wide invariants — Fly.io single region on
  managed Postgres, CLI as an operator tool with local SQLite dropped, UUIDv7 app-side with no
  DB-side default, S3-compatible storage behind `StorageProvider` (never a Fly volume),
  transport-only `EmailProvider` — are **decisions**. None exist in the tree: `config.py:14`
  still hardcodes `DB_URL = f"sqlite:///{DB_PATH}"` and no Postgres driver is installed.
- **Divergence compounds.** SPEC-002 is load-bearing; every spec above it describes SPEC-002's
  *design*, not code. If SPEC-002 is implemented differently than specified, all of them inherit
  the difference. Re-verify §4 and §5 against the tree before building anything downstream of it.
- **`tasks/todo.md` is not a task list for this work.** It was last updated 2026-05-14, tracks
  the old single-user CLI product, and uses a **colliding phase-numbering scheme**. Mirror the
  active DAG into a new `## ACTIVE:` section; never read the rest of it as input.

### 6.1 The `conftest.py` rule is per-spec, and the two specs disagree on purpose

- **SPEC-001:** add a `pg_session` fixture, and **do not change the existing `session` fixture**
  — its §9 says the existing tests depend on its current behaviour.
- **SPEC-002 Step 15:** *does* replace `conftest.py`'s engine — but keeps the `session` fixture's
  **name and semantics**, now yielding an account-scoped session, precisely because 28 of 33
  files use it.

Both are correct for their phase. Recorded here so the pilot's lesson ("don't touch `session`")
is not generalized into a rule that blocks SPEC-002.

---

## 7. Environment

### CI exists as of the SPEC-002 pre-flight (2026-08-10)

**Previously there was none**, and the SPEC-001 pilot ran every gate locally while recording that
per-PR enforcement was unproven — acceptable there because no SPEC-001 criterion depended on a
runner.

SPEC-002 changed that. **A21 is "the phase's definition of done"**, Step 17 requires it *"on every
PR"*, and A23's gate is literally *"full suite green in CI"*. Six later specs inherit the
tenant-isolation invariant those describe, so proving it once on one machine is not enough.
`.github/workflows/test.yml` now runs on push and pull_request with:

- a **`postgres:18` service** with a `pg_isready` health gate — matching the dev machine's 18.4, so
  a green local run and a green CI run mean the same thing. SPEC-002 §9: *"the isolation test
  cannot run on SQLite: the raw-SQL cases are defended by RLS alone."*
- **Python 3.11 and 3.12.** 3.11 is not redundant — it is the declared floor, and
  `mihomes.ids.new_id()` takes a *different code path* below 3.14. SPEC-001 A2 exists for that.
- `DATABASE_URL`, `MIGRATION_DATABASE_URL` and `TEST_DATABASE_URL` set separately, per SPEC-002
  §10's deliberate split: the app must not connect as the owner (N5), but Alembic must.
- **A skip-detection step.** `TEST_DATABASE_URL` is set, so the Postgres-dependent tests must
  *run*. A skipped test still exits 0 (§0), which would let CI report green while the criteria that
  prove tenant isolation never executed — the exact false success the workflow was added to
  prevent.

**Lint is scoped to spec-introduced files**, selected by `git diff` against the merge-base so it
stays self-maintaining. `ruff check .` reports **565 findings across the pre-existing tree** and
**none in SPEC-001/002 code** — the pre-commit hook only ever linted *staged* files, so the older
tree was never checked whole. Cleaning that is a real task on its own branch, logged in
`opportunities.md`; burying it in the tenancy diff would make both unreviewable.

### What the harness still cannot close

Nothing in the runner proves *production* behaviour. Deploy-time prerequisites (Fly, DNS, a
verified Resend domain) remain human actions, reported as unmet launch gates rather than silently
assumed — see §3.2 and §3.3.
