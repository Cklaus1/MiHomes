# SPEC-005 build-loop report — Phase 4: Polish + Email Lifecycle + GA

**Status:** all 17 steps built; 36/36 acceptance criteria gated, every node id resolving.
**Condition C (full suite) is the remaining gate** — its first run was red (§3.6), the cause was
fixed, and the confirming run is recorded in §6. Do not read this report as "complete" until §6
carries a green count.
**Branch:** `worktree-spec-build-harness`. **Base:** `661f6f5` (SPEC-004 complete).
**Spec:** `docs/specs/SPEC-005-phase4-polish-email-ga.md`

---

## 1. What shipped

| Group | Step | What landed |
|---|---|---|
| G1 | 1 | `headers` on the `EmailProvider` Protocol — the set's only widening (D11) |
| G2 | 2 | Suppression at the `_send` choke point, the `klass` argument, HMAC unsubscribe tokens |
| G3 | 3 | `EmailDelivery` — the third observability surface (D7) |
| G4 | 4 | `EmailOutbox`, `enqueue`/`drain`, the backoff ladder (D12) |
| G5 | 5 | Four workloads on `mihomes jobs`; `SCHEDULE` as one source of truth |
| G6 | 6 | The Phase 4 tables, and the first test in this repo to run real Alembic |
| G7 | 7 | `privacy/export.py` — ORM-assembled, tenant-scoped (D14) |
| G8 | 8 | The deletion state machine and the three-disposition purge (D15/D18) |
| G9 | 9 | RFC 8058 one-click unsubscribe |
| G10 | 10 | The dunning ladder on the outbox |
| G11 | 11 | `campaigns.py`, enrolment on account creation, the `drips` job |
| **G12** | **12** | **The two Estate gates — and four defects in code that already claimed to be Step 12** |
| **G13** | **13** | **The weekly digest, gated as a send (D16)** |
| **G14** | **14** | **`audit_export` at the route and the CLI, carrying `upgrade_target` (A34)** |
| **G15** | **15** | **One `dictConfig`, `web/errors.py`, `/healthz`, and nine silent swallows closed** |
| **G16** | **16** | **The D17 deliverability documentation test** |
| **G17** | **17** | **`mihomes ga-readiness` — the exit criterion (A33)** |

Bold rows are this session's work (G1–G11 landed earlier in the run).

---

## 2. Stop condition

| | Condition | Result |
|---|---|---|
| **A** | Every checkbox `[x]` or `[!]` | ✅ — G5.3 is `[!]` (U10, infra half unanswerable from the repo) |
| **B** | Every §6 step tasked, every §8 criterion gated | ✅ — 17 steps; `spec005_reconcile.py --collect` exits 0 |
| **C** | Full suite green including this spec's tests | ⏳ — **red on the first run** (9 failed, from this phase's own logging config); fixed, confirming run in §6 |
| **D** | Smoke green | ✅ — `test_smoke_all_tools.py` |
| **E** | Every §8 criterion green by its own named test | ✅ — **36/36 node ids resolve** |

---

## 3. The findings that mattered

Every defect below was **invisible to reading and immediate under execution or mutation**. That
is the single most useful thing this run produced, and it recurred often enough to be a rule
rather than an anecdote.

### 3.1 A gate that gated nothing (G12)

`predictive_maintenance.py` called `check_entitlement(account, "predictive_maintenance")` — an
entitlement **key** where `can()` keys on **actions**. The string matched neither dictionary,
fell through to `can()`'s closing `return Allowed()`, and **allowed every plan on every call**.

The module's docstring already read *"Step 12 (SPEC-005 Phase 4): gated on Estate plan"*.

**The spec's own §5.5 carries the same wrong string**, as did the abandoned pre-G12 work found in
the stash. An A12 written faithfully from the spec would have passed vacuously against a dead
gate — which is why the criterion is asserted against `PLAN_LIMITS` rather than against the
literal booleans a reader would transcribe.

### 3.2 A denial that named a plan which also denies (G12)

`can()` resolves through `limits_for` (default `PLAN_LIMITS`); `_upgrade_target` defaulted to
`PLAN_LIMITS_PHASE3`, whose overrides granted `audit_export` to Free **and** Pro. Measured: a
Free account denied `audit.export` was told to upgrade to **Pro, which also denies it** — exactly
the failure `_upgrade_target`'s own docstring says it exists to prevent, arriving through the
defaults rather than through the walk.

Fixed at the default (the class of bug), not by deleting the override (this instance).

### 3.3 A pre-flight that verified something untrue (G12)

§0.6 recorded the three Estate keys as ✅ *"`False` on Free **and Pro**"*. `PLAN_LIMITS["pro"]`
carried `predictive_maintenance: True`, contradicting `PRICING:89` and D10. **Third instance of
§0.5's shape in this run, and the second to land in the checking rather than the code.**

### 3.4 Three of my own tests were green with the thing they test deleted (BD19)

| Group | The vacuous assertion | Why it held |
|---|---|---|
| G8 | A10 asserted zero storage deletions | The seeded `file_path` was never a storage key |
| G13 | A14 asserted the job "does not raise" | With the stub never called, that was true either way |
| G14 | A34 grepped the route for `upgrade_target` | It still appeared — in the log line |

All three share one shape: **an assertion whose subject was absent.** The rule earned, stated as
a check rather than a caution: *when an assertion is negative — did not raise, was not called, is
None — ask what would make it vacuously true, and make the positive case a parameter of the same
test.*

### 3.5 Nine silent swallows, and not one was `except Exception: pass` (G15)

The shape everyone greps for did not occur once. What occurred nine times was a handler that set
a **user-facing** error string and told the operator nothing. Those are not crashes, which is why
a hardening pass looking for crashes left every one — and they are precisely N15's failure.

Found by an AST walk asking *does this handler log or re-raise*. A grep for `pass` finds zero.

### 3.6 The full suite caught what nine group-scoped runs could not

**F.1's first run came back 9 failed, 2394 passed** — every failure caused by `logging_config.py`,
the file G15 introduced and G15's own tests had passed on. Both bugs were in the *configuration*
rather than in any code under test:

- A console `StreamHandler` bound `sys.stderr` at construction. Once pytest swapped the stream,
  every emit raised `ValueError: I/O operation on closed file` — and **a failed emit aborts the
  record before the remaining handlers run**, so the durable file handler lost it too. Nine tests
  asserting "this failure was logged" read as *the code did not log*, pointing at seven modules
  that had not changed.
- `propagate: False` meant nothing on the root logger ever saw a record: not `caplog`, not a
  script's `basicConfig`, not an operator's handler, not an aggregator. An observability change
  that hid its own output from every external consumer.

**The process lesson is the transferable one.** G15's tests passed, the web suite passed,
`test_logging.py` passed — none of them *could* see this, because the interaction only exists
when one module's global config outlives another module's test. A change to global state is not
done until the global gate has run, which is exactly what condition C is for. Recorded as BD21.

### 3.7 The presentation layer, three times (G15, G17)

- **`/healthz` raised `ImportError` on every call** — `mihomes.db` exposes `get_engine()`, not a
  module-level `engine`.
- **The HTML 500 carried no `X-Request-ID`** — an unhandled exception unwinds *past* the
  middleware, so its header assignment never runs. On the one response a support ticket needs it.
- **Rich deleted the text it was asked to print** — two GA bullets open with `**[regression
  check, not new work]**`, and `rprint` parses `[...]` as a style tag.

Plus **the tenant gate for the third time** (BD7's shape): `ga-readiness` reads a markdown file
and no database, but inherited the root callback's account gate, so on a multi-account install
the command answering *"can we launch"* exited 1 with a list of account slugs.

Every one of these is invisible to a unit test of the underlying service. That is why A33 is
driven through the Typer app rather than by calling `render()`.

### 3.8 The harness's own count was wrong (G17)

The GA definition of done was recorded as five bullets. It is **six**. `ga_readiness.py` parses
them from `SAAS_PRD.md`, so the miscount surfaced on the first run instead of being frozen into
the gate and under-reporting GA by one requirement forever — N5's rule arriving in the readiness
surface rather than in the export or the purge.

---

## 4. Deviations from the spec

Recorded in full at `tasks/build-loop-spec005.md` §2.2 as BD1–BD20. The three that a reader of
the spec most needs:

- **BD15** — §5.2 lists `send_dunning` as lifecycle; it ships **transactional**. Under D13
  suppression is absolute for lifecycle mail, so an unsubscribed customer would be told once that
  their card failed and then silenced while their access lapsed.
- **BD18** — §5.5's `predictive_maintenance.run` is not a valid action string. The action is
  `maintenance.predict`. §5.5 should be corrected before anyone builds from it again.
- **BD3/BD4** — the spec contradicts itself on `drain`'s signature (a global sweep returns zero
  rows under RLS) and on render timing (§5.2 says render-then-enqueue; §4.1's load-bearing column
  comment says the opposite). §4.1 wins.

---

## 5. What GA ships with — unresolved

`mihomes ga-readiness` is the live surface; this is its state at the end of the run.

**3 of 6 met, 3 blocked, all three on the founder:**

| Gate | Status | Why |
|---|---|---|
| Phase 1–3 exit criteria still green | ✅ met | Regression check (B2) |
| Full email lifecycle + DKIM/SPF/DMARC passing | ⛔ blocked | The lifecycle is built. **No sending domain is verified**, so "passing" is unprovable here — A20 asserts the *documented* record is consistent (D17, U7) |
| Downgrade/past-due grace policy | ✅ met | Regression check (B2), SPEC-004 Step 14 |
| Data export + account deletion | ✅ met | SPEC-005 Steps 7–8 (A27/A28) |
| ToS + Privacy Policy published | ⛔ blocked | **SPEC-001 O1** — the oldest unresolved item in the set (U1) |
| Public signup at real prices | ⛔ blocked | **SPEC-004 O1** — ~20 `PLACEHOLDER` values (U2) |

Also carried, and **not** made safe by this phase (§10):

- **O1** (drip content) and **O2** (deletion grace length) — both open; the mechanisms ship and
  the copy/config does not.
- **U6** — no Stripe account, so the dunning ladder's *live* behaviour is unproven.
- **U10** — Fly's scheduled-machine mechanism is still unverified against their documentation.
  The interface half ships and A15/A17 prove it; the infra half cannot be answered from the repo.
- **U11** — the ~138 `except Exception` blocks **outside** the request path. A32 is scoped to
  `web/` per C3; the rest is real cleanup with no acceptance criterion.
- **Observability is instrumentation, not alerting.** The system is now legible — one logging
  config, structured records, real error handlers, a request id. **Nobody is paged.**

---

## 6. Verification

- **F.1** — full suite. **Red on its first run (9 failed / 2394 passed), all from this
  phase's own logging config**; fixed and re-run — see §3.6 and BD21.
- **F.2** — all 36 §8 criteria green by their own node ids (`--collect` resolves 36/36).
- **F.3a** — 17 of 17 §6 steps tasked.
- **F.3b** — `py scripts/spec005_reconcile.py --collect` exits 0.
- **F.4** — `tests/integration/test_smoke_all_tools.py` green.
- **Mutation checks** — every security-, money- and privacy-relevant arm in G12–G17 was broken,
  confirmed RED for its own reason, and restored. One mutation (M4) **survived** and exposed a
  vacuous test, which is recorded above as BD19.

---

## 7. For whoever picks this up

1. **`SPEC-005` §5.5 is wrong** and will produce a dead gate if built from verbatim (BD18).
2. **Three founder decisions block launch**, not the build. `mihomes ga-readiness` names them and
   exits 1 while any is outstanding.
3. **The drips ship against placeholder templates.** A green Step 11 means drips *can* send, not
   that anything worth sending exists (O1).
4. **Nothing here proves mail is delivered.** Verify the sending domain, then send a real message
   and confirm inbox placement — `GTM` §5's checklist item, which no test replaces.
