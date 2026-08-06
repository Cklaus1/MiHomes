# SPEC-001 Build Loop — Phase 0: Landing Page + Waitlist

> **Input spec:** `docs/specs/SPEC-001-phase0-landing-waitlist.md` (680 lines, *Ready to build*)
> **Conventions:** `tasks/build-loop-conventions.md` — all mechanisms (stop condition, poison
> ceiling, circuit breaker, artifact routing) are defined there and inherited here.
> **Branch:** `spec-build` (from `origin/main` @ `be8d398`). **Target ref for all code claims:**
> `origin/main`.
> **Invocation:** `/loop tasks/build-loop-spec001.md`

**This is the pilot.** SPEC-001 is the root of the dependency chain — the only spec with no
`Depends on:` line. SPEC-002 through SPEC-008 each declare a dependency on it, directly or
transitively. Its harness is authored first, run first, and its lessons feed the authoring of
the remaining six.

**What this phase builds:** a standalone marketing app at `mihomes.ai` that captures a waitlist
and confirms by email. **What it explicitly does not build:** no `users` table, no sessions, no
tenancy, no billing. The spec's own words — *"`SAAS_PRD:125` is explicit that there is no
`users` table before Phase 1, and nothing here creates one."*

---

## 0. Prerequisites — halt before task 1 if unmet

Per conventions §3.2, a missing prerequisite halts the run **before** the first task, with the
missing list named. It does not poison tasks one at a time.

| # | Prerequisite | Needed by | Check |
|---|---|---|---|
| P1 | A reachable Postgres, `TEST_DATABASE_URL` set | Step 2 (A3), all integration tests | `psql "$TEST_DATABASE_URL" -c 'select 1'` |
| P2 | `psycopg[binary]` installed | same | `python -c "import psycopg"` |
| P3 | Fly app provisioned, DNS delegated, Resend domain verified | **Step 9 only** | manual |

**P1/P2 halt the run.** D3 makes Phase 0 Postgres-only — *"Phase 0 does not use SQLite —
starting on the target engine avoids a pointless migration two weeks later."* No SQLite
fallback is permitted; substituting one would satisfy A3 against the wrong engine.

**P3 does not halt.** Step 9 is documentation plus config files, and its only automated
criterion (A18) is a grep-level docs test. If P3 is unmet, G9 still runs and produces the
artifacts; the deploy itself is a human action recorded in the report as an unmet launch gate.

**Environment (spec §10):** `DATABASE_URL`, `RESEND_API_KEY`, `EMAIL_PROVIDER`
(`console` in dev/CI), `GOOGLE_CLIENT_ID` / `_SECRET`, `SECRET_KEY`, `BASE_URL`.
Use `EMAIL_PROVIDER=console` for the whole run — `ConsoleProvider` sends nothing.

---

## 0.1 Stop condition for this spec

Per conventions §0, all five conditions. Restated concretely here:

| | Condition | For SPEC-001 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 below |
| **B** | every §6 step has a task; every §8 criterion has a gate | F.3a + F.3b |
| **C** | full suite green | `pytest -q` — **1080 existing tests must stay green**, plus this spec's new ones |
| **D** | smoke green | **N/A — no smoke target.** Phase 0 ships a separate app (D1); neither `test_smoke_all_tools.py` nor `test_web_smoke.py` covers it. Declared absent per conventions §0.1 rather than silently dropped. A11 is the closest equivalent and is bound in G5. |
| **E** | every §8 criterion green by its own named test | all 18, F.2 |

**On the baseline count:** the spec's §9 says *"the 780+ existing tests depend on it"* — that was
`telegram-bot`. On `origin/main` the suite collects **1080** across **82** files. Pre-flight
(conventions §3.1) must record this before G1 starts. The number matters because condition C is
"nothing regressed," and the wrong baseline makes that unfalsifiable.

**Measured baseline at authoring time (2026-08-06, branch base `be8d398`):**

```
py -m pytest -q --co   →  1080 tests collected in 11.07s
```

Condition C means **≥ 1080 passing**, plus this spec's new tests. A run that ends with 1079 has
regressed something regardless of what else is green.

> **Invoke pytest as `py -m pytest`, not `python -m pytest`.** On this machine `python` resolves
> to the Microsoft Store shim and fails with *"Python was not found"*. Recorded here so the loop
> does not burn its 3-attempt poison ceiling on a launcher error rather than a real failure.

---

## 1. Task DAG

Nine groups, one per §6 step — the spec states *"Each step is independently verifiable and
separately committable"*, so each step is its own commit and its own resume point
(conventions §1.3). Dependencies are top-to-bottom.

### [ ] G1 — `mihomes.ids` — *no deps*

- [ ] G1.1 · §6 Step 1 · A1 · write `src/mihomes/ids.py` — `new_id() -> uuid.UUID` (UUIDv7, app-side; §4.1). **No DB-side default** — `gen_random_uuid()` emits v4 and would destroy v7 index locality (D5) · verify: `tests/unit/test_ids.py::test_uuid7_properties` (1,000 ids unique, byte-sort == creation order, `.version == 7`)
- [ ] G1.2 · §6 Step 1 · A2 · the 3.11 fallback path — `new_id()` must work on the declared floor, not only 3.14 · verify: `tests/unit/test_ids.py::test_fallback_generates_valid_v7`

> A2 is not redundant with A1. `uuid.uuid7()` is 3.14+; the project floor is 3.11. The fallback
> is the code path that will actually run, so it needs its own test.

### [ ] G2 — `Waitlist` model + migration — *dep: G1*

- [ ] G2.1 · §6 Step 2 · — · `src/mihomes/models/waitlist.py` (§4.2) + register in `models/__init__.py`. **Global table: no `account_id`, no RLS** (D4) — it ships before `accounts` exists · verify: `tests/unit/test_waitlist_model.py` (column types, nullability, unique constraint on email)
- [ ] G2.2 · §6 Step 2 · A3 · `alembic/versions/xxxx_waitlist.py` (§4.3) · verify: `tests/integration/test_migration_waitlist.py::test_upgrade_downgrade`

**Extra gate (conventions §2) — migration round-trip.** `alembic upgrade head` → `downgrade` →
`upgrade`, clean, against real Postgres. Damage here is to *state*, and Phase 1 rides on this
table. Note the existing 40 revisions are SQLite-batch-mode; this is the first Postgres
migration in the tree.

### [ ] G3 — email package — *dep: G1*

- [ ] G3.1 · §6 Step 3 · A9 · Protocol + exceptions + `EmailResult` + factory (`provider.py`), `ConsoleProvider`, `ResendProvider` · verify: `tests/unit/test_email_provider.py::test_unknown_provider_raises`
- [ ] G3.2 · §6 Step 3 · A8 · `render.py` → `(subject, html, text)`, `service.py`, and the three templates · verify: `tests/unit/test_email_render.py::test_waitlist_confirmation_has_both_parts`

> **Build this to final quality now.** The spec is emphatic: this is the one Phase-0 artifact
> reused **verbatim** in Phases 2–4 — welcome, invites, receipts and dunning all ride on it
> (`BILLING` §1). SPEC-005 D11 makes the set's only widening of it, one additive `headers`
> kwarg. Under-building here is paid for four times.

### [ ] G4 — waitlist service — *dep: G2, G3*

- [ ] G4.1 · §6 Step 4 · A4 · `normalize_email` + `signup` — idempotent per email; a duplicate **updates** the existing row, never creates a second · verify: `tests/unit/test_waitlist_service.py::test_signup_is_idempotent`
- [ ] G4.2 · §6 Step 4 · A5 · token generation — **only the hash is persisted**; assert the raw token appears nowhere in the row · verify: `tests/unit/test_waitlist_service.py::test_token_stored_hashed_only`
- [ ] G4.3 · §6 Step 4 · A6,A7 · `confirm` — sets `confirmed_at`, second confirm is a no-op, expired/unknown token does not confirm · verify: `tests/unit/test_waitlist_service.py::test_confirm_idempotent`, `::test_confirm_rejects_bad_token`
- [ ] G4.4 · §6 Step 4 · — · `position`, `confirmed_count`. **O4 default: compute it, do not display it** · verify: `tests/unit/test_waitlist_service.py`

### [ ] G5 — landing app skeleton — *dep: G1*

- [ ] G5.1 · §6 Step 5 · A17 · `create_landing_app()`, `/healthz`, `mihomes-landing` entry point in `pyproject.toml` · verify: `tests/integration/test_landing_app.py::test_healthz` (200 with DB reachable)
- [ ] G5.2 · §6 Step 5 · A11 · **prove the single-user app is not mounted** — `GET /properties` returns 404 (§7-N1, D1) · verify: `tests/integration/test_landing_app.py::test_existing_routes_are_404`
- [ ] G5.3 · §6 Step 5 · A13 · `ratelimit.py` — in-process per-IP token bucket on `POST /waitlist` and the OAuth callback (D10) · verify: `tests/unit/test_ratelimit.py::test_burst_is_limited` (429 past threshold, per-IP isolation)

> **A11 is the structural invariant of this phase.** D1 chose a standalone app over a route in
> the existing one precisely because the existing app is *"the single-user product with 23 route
> modules and **no authentication**."* If a single existing route is reachable from the landing
> app, Phase 0 has published an unauthenticated estate-management system to the public internet.
> Treat a red A11 as a stop-the-run defect, not an ordinary failure.

### [ ] G6 — templates + `GET /` — *dep: G5*

- [ ] G6.1 · §6 Step 6 · — · `base.html` + `index.html` — the nine sections (`GTM` §2.1–2.9), inlined critical CSS, one static `hero.svg`, **no JS framework** · verify: `tests/integration/test_landing_page.py` (sections present)
- [ ] G6.2 · §6 Step 6 · A16 · **no dollar figures anywhere** — plan *shapes* only (Free/Pro/Estate), because every price in `PRICING_AND_PACKAGING.md` is still `PLACEHOLDER` (D14) · verify: `tests/integration/test_landing_page.py::test_no_prices_rendered`
- [ ] G6.3 · §6 Step 6 · — · chat-intake card shows **Telegram only, or is omitted** — WhatsApp Baileys pairing is broken and Twilio is post-GA; advertising either is vaporware (D15) · verify: `tests/integration/test_landing_page.py` (no WhatsApp mention)

> Footer links to ToS and Privacy **will 404 until O1 lands**. That is expected and documented
> (§1.3 O1) — do not stub fake pages to make them resolve.

### [ ] G7 — `POST /waitlist` + confirm route — *dep: G4, G6*

- [ ] G7.1 · §6 Step 7 · — · wire the form to `signup()`, send via `EmailService`, implement `GET /waitlist/confirm`. Full loop against `ConsoleProvider`: submit → token in console → GET confirm → `confirmed_at` set (D7 double opt-in) · verify: `tests/integration/test_waitlist_routes.py`
- [ ] G7.2 · §6 Step 7 · A10 · **a send failure must not roll back the signup** · verify: `tests/integration/test_waitlist_routes.py::test_signup_survives_email_failure`
- [ ] G7.3 · §6 Step 7 · A12 · **no email enumeration** — the response is byte-identical for a new and an existing address · verify: `tests/integration/test_waitlist_routes.py::test_no_email_enumeration`

### [ ] G8 — Google OAuth stub — *dep: G7*

- [ ] G8.1 · §6 Step 8 · A14 · `/auth/google/start` + `/auth/google/callback` — a valid ID token creates a `waitlist` row with `source='google'` and **nothing else: no `users` row, no session cookie** (D8) · verify: `tests/integration/test_oauth_stub.py::test_callback_creates_waitlist_row_only`
- [ ] G8.2 · §6 Step 8 · A15 · a token with a bad signature is rejected · verify: `tests/integration/test_oauth_stub.py::test_callback_rejects_forged_token`

> "Stub" here means *scope*, not *rigor*: signature verification is real. The stub-ness is that
> it writes a waitlist row and stops.

### [ ] G9 — deploy artifacts — *dep: G5* · *P3 may be unmet; see §0*

- [ ] G9.1 · §6 Step 9 · — · `Dockerfile`, `fly.toml`, `.dockerignore`. **Migrations as a release command, never on boot** (D9) — the existing app's `init_db()` on startup is a race when Fly runs several machines (§7-N4) · verify: image builds; release command present in `fly.toml`
- [ ] G9.2 · §6 Step 9 · A18 · `docs/deploy/PHASE0-DEPLOY.md` with the DNS table. **DMARC = `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai`, without `adkim=s; aspf=s`** (D11) · verify: `tests/unit/test_deploy_docs.py::test_dmarc_relaxed_alignment`
- [ ] G9.3 · §6 Step 9 · — · the pre-launch checklist, carrying O1/O2/O3 as unchecked items · verify: checklist present with all seven rows

> **A18 is a docs test on purpose.** `PRD_REVIEW` A6 found `GTM:273` publishing a
> copy-pasteable *wrong* DMARC value that contradicts `BILLING:224`. Strict alignment breaks
> legitimately-signed Resend mail because the return-path sits on its own sub-label. A
> grep-level test is the cheapest way to stop the wrong value coming back.

### [ ] G-Final — compound-stop verification — *dep: all*

- [ ] F.1 · full-suite `pytest -q` green, no regression against the pre-flight baseline (condition C)
- [ ] F.2 · all **18** §8 criteria green, each by the test named in its own row (condition E)
- [ ] F.3a · walk §6 top-to-bottom: every one of the **9** steps has a task (condition B, steps)
- [ ] F.3b · walk §8 top-to-bottom: every one of the **18** criteria has a gate (condition B, criteria)
- [ ] F.4 · write `tasks/build-loop-spec001-report.md` (conventions §5)

---

## 2. Criteria → group map

Condition B's F.3b reconciles against this. Every `A`-label from §8 must appear exactly once.

| Criterion | Group | Criterion | Group |
|---|---|---|---|
| A1 | G1.1 | A10 | G7.2 |
| A2 | G1.2 | A11 | G5.2 |
| A3 | G2.2 | A12 | G7.3 |
| A4 | G4.1 | A13 | G5.3 |
| A5 | G4.2 | A14 | G8.1 |
| A6 | G4.3 | A15 | G8.2 |
| A7 | G4.3 | A16 | G6.2 |
| A8 | G3.2 | A17 | G5.1 |
| A9 | G3.1 | A18 | G9.2 |

**18/18 bound.** A criterion appearing in no group is a defect in this harness, not in the spec —
that is exactly what F.3b exists to catch.

---

## 3. Spec-specific non-negotiables

Beyond conventions §6:

- **Do not touch the single-user app.** The spec's §3 is explicit: *"Not modified:
  `src/mihomes/web/app.py`, `src/mihomes/web/server.py`, and every existing route module."*
  Phase 0 shares the stack and nothing else (D1, §7-N1).
- **Do not change the existing `session` fixture.** Add a `pg_session` fixture alongside it.
  SPEC-001 §9 is explicit. Note this rule is **SPEC-001-scoped** — SPEC-002 Step 15 deliberately
  rewrites what `session` yields (conventions §6.1). Do not generalize it.
- **No SQLite anywhere in Phase 0** (D3). Not as a test fallback, not as a dev convenience.
- **Nothing creates a `users` row or a session cookie** (D8, and the phase goal). A14 tests for
  its absence.
- **No dollar figures** (D14). No WhatsApp in the chat card (D15).
- **Migrations never run on boot** (D9).
- **`resend`, `psycopg[binary]`, `authlib`, `itsdangerous`** are the only new dependencies, plus
  the `mihomes-landing` script entry. Anything else is scope creep → `opportunities.md`.

---

## 4. Open decisions — all blocks-ship, none blocks-build

Per conventions §3.3. SPEC-001 §1.3 states it directly: *"None of these block **writing code**.
Each blocks **launching**."*

| # | Question | Disposition during the run |
|---|---|---|
| O1 | ToS + Privacy published | Footer links 404. Build proceeds; **do not stub fake pages.** |
| O2 | Founding-member offer terms | Copy says only "a founding-member offer". Do not invent terms. |
| O3 | Waitlist gate number | Phase 0→1 transition, not the build. Ignore during the run. |
| O4 | Show queue position publicly? | **Default: compute it, do not display it.** Schema supports either. |

None poison a task. All four are carried into the report as unmet launch gates, and O1/O2/O3
appear in Step 9's pre-launch checklist as unchecked items.

---

## 5. Known gaps this run cannot close

- **No CI** (conventions §7). Gates run locally via `pytest -q`. SPEC-001 has no criterion whose
  wording requires a CI runner, so nothing here is downgraded — but the gap is logged in
  `opportunities.md` because SPEC-002 A23 and Step 17 *do* require it, and that is the next spec.
- **P3 infrastructure** (Fly, DNS, Resend) is a human action. G9 produces the artifacts; the
  deploy is recorded as an unmet launch gate.
