# SPEC-010 Build Loop — Email/password authentication

> **Input spec:** `docs/specs/SPEC-010-email-password-auth.md` (**2 open decisions** — O1 dual
> identity linking, O2 password policy; neither blocks the build)
> **Conventions:** `tasks/build-loop-conventions.md` — inherited **unchanged**.
> **Branch:** `worktree-spec-build-harness`. **Target ref:** HEAD `b5145ec`.
> **Status: AUTHORED, NOT RUN.**

**The stake:** every other spec in this set moves data behind an already-solved front door.
**This one builds the door.** A password bug is not a degraded feature — it is unauthorised
access to an estate's records, staff roster and financials, and the failure is silent: the
attacker looks exactly like the owner.

**Exit criterion:** A15 — signup → onboarding → sign out → sign in → forgotten-password reset →
sign in with the new password.

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, four DB env vars | ✅ |
| P2 | `MIHOMES_SECRET_KEY` set | ✅ required since SPEC-003 U1 |
| P3 | **`scrypt` importable from the declared `cryptography`** | ✅ **measured** — `cryptography 46.0.6`, `Scrypt(...).derive()` returns 32 bytes. **No new dependency** (D4) |
| P4 | `EmailProvider` + the `console` provider, for reset mail | ✅ SPEC-005; use `console`, never a mocked `resend` |
| P5 | The onboarding wizard, for signup to route into | ✅ SPEC-003, reachable since `885a264` |
| P6 | **Founder confirmation that `SAAS_PRD:105` is being reversed** | ⚠️ **assumed, not obtained** — §0.2 |
| P7 | The ~200-commit merge to `main` | ❌ open, not this run's job — U1 |

**Environment:** as every prior harness. `py -m pytest`, never `python`.

---

## 0.2 P6 — this run reverses a documented scope decision

`SAAS_PRD:105` lists *"Non-Google auth"* as out of scope for GA.
`ONBOARDING_AUTH_RBAC:11` says it **owns** authentication and `:60` promises email/password can
be added. **The two documents disagree**, and D1 resolves in favour of the owning document.

**That is a product reversal, not a technical one.** The founder asked for password login and
approved a spec-first approach; that is the authority this run proceeds on. It is recorded here
rather than treated as settled, because a future reader finding `SAAS_PRD:105` unamended would
otherwise conclude the build ignored it. G0's B1 amends it.

---

## 0.3 What makes this harness different

**The failure mode is not a broken feature — it is a working one that is unsafe.** Every prior
spec's risk was a step not done, or done vacuously. Here:

* **A2 cannot be verified by using the product.** A sha256-hashed password logs in exactly like
  a scrypt-hashed one. Every functional test passes either way, and the difference only appears
  when a database leaks — at which point every user's password is already gone.
* **Three criteria assert an absence of information** — A3, A7 and A11 all require that the
  system does **not** reveal whether an email exists. A response that is *helpful* fails them,
  which is the opposite of the usual bug.
* **The codebase's own comments recommend the wrong primitive.** `sessions.py:9` says *"No salt
  and no bcrypt, deliberately"* — correct for a 256-bit token, wrong for a password. A reviewer
  copying the adjacent pattern is the single most likely way this goes wrong (§3 G-kdf).

---

## 0.4 Stop condition

| | Condition | For SPEC-010 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 |
| **B** | every §6 step tasked, every §8 criterion gated | F.3a + F.3b |
| **C** | full suite green including this spec's tests | baseline **2707 passed** |
| **D** | smoke green | `test_smoke_all_tools.py` |
| **E** | every criterion green by its own named test | **all 15**, F.2 |

**Baseline — HEAD `b5145ec`:** `2707 passed, 3 skipped, 2 xfailed, 0 failed`. **A new skip is
red** — and in this spec a skipped security assertion is worse than elsewhere: `test_auth.py:318`
already records that a conditionally-skipped cookie-flag test was *"a red gate"*.

---

## 0.5 The gates this spec must not break

Measured, with exact locations. **Three of them defend properties this spec deliberately
changes**, so each needs an edit in the same commit as its migration — never after.

| Gate | Location | What SPEC-010 does |
|---|---|---|
| `google_sub` is NOT NULL | `tests/unit/test_membership.py:95` | **Inverts** (D6). Edit with the reason, in G2's commit |
| `google_sub` is unique | `test_membership.py:94` | **Unchanged** — a nullable unique column is fine in Postgres; multiple NULLs do not collide |
| **email must NOT be unique** | `test_membership.py:103` | **Unchanged, and that is the point.** D3's partial index leaves the *column* non-unique. If this test needs editing, the design went wrong |
| same email, different sub = different people | `test_auth.py:283` | **Unchanged.** Google identity is untouched |
| table count | `tests/integration/test_pg_baseline.py:149` | **56 → 57** (`password_reset_tokens`), in the migration's commit |
| metadata drift | `test_pg_baseline.py:163` | The partial index must be declared on the model too, or `compare_metadata` fails |
| route declarations | `tests/unit/test_route_declarations.py` | New module `web.routes.password` needs `PERMANENT_ALLOWLIST` **and** `ALLOWLIST_MECHANISMS` entries — the test asserts set equality both ways |
| **`auth`'s mechanism string** | `test_route_declarations.py:90-93` | Currently *"the OIDC provider's own flow"*. **Becomes false** when password routes land. Nothing fails automatically — the test only length-checks the prose — so this is correctness debt to retire deliberately |
| A13 (SPEC-009) | `test_ui_build.py` | Every new template means `npm run build:css && npm run stamp` |
| A11 (SPEC-009) | `test_ui_responsive.py` | Every new template must be added to `docs/UI_MANUAL_CHECKLIST.md` |

---

## 0.6 PRE-FLIGHT — re-verify before G1

Conventions §3.1. The spec was written against `c9e2b77` and this harness against `b5145ec`, so
its claims are current by construction. **Re-run these four anyway**, because they are the ones
the whole design rests on:

1. `users.google_sub` is NOT NULL and uniquely indexed (`user.py:30`, `0001:99,108`)
2. `users.email` is **not** unique (`0001:107`), and two tests assert it
3. `create_session(db, user_id)` takes no provider argument (`sessions.py:96`)
4. **No password-hashing library is declared** — repo-wide grep for `argon2|bcrypt|passlib`
   returns exactly one hit, and it is the prose comment at `sessions.py:9`

**Halt on any mismatch.** If (2) has changed, D3's partial index may be unnecessary — or already
impossible.

---

## 0.7 O1 and O2 — neither blocks the build

- **O1 — may one person hold both a password and a Google identity?** Blocks the **collision
  path only**. Build refusing it with a clear message; linking is additive and
  `ONBOARDING:314` Q1 already defers it. **U2**.
- **O2 — the password policy.** Blocks nothing. Build with the documented default: 12
  characters, no composition rules. **U3**.

---

## 0.8 UNMET LAUNCH GATES

| # | What | Owner |
|---|---|---|
| **U1** | ~200 commits unmerged to `main` | founder |
| **U2** | O1 — dual identity linking | founder |
| **U3** | O2 — password policy; no breach-corpus check (§10) | founder |
| **U4** | **No MFA.** A stolen password is full access | founder |
| **U5** | **The rate limiter is in-process.** On multiple instances the effective limit multiplies by instance count — adequate single-instance, needs a shared store before scaling out | founder |
| **U6** | Everything SPEC-005 §10, SPEC-006 §0.8, SPEC-008 §0.8 and SPEC-009 §5 carry | founder |

---

## 1. Task DAG

`py scripts/spec010_reconcile.py --collect` after every group commit.

**Ordering the spec names as load-bearing:** **Step 2 before Step 3** (the schema exists before
routes write to it); **Step 3 before Step 5** (an account exists before its password can be
reset).

### [x] G0 — the doc repairs — *dep: none*
- [x] G0.1 · §2 B1 · — · `SAAS_PRD:105` — **narrow, do not delete**: email/password ships, additional third-party IdPs stay out. Same shape as SPEC-009's native-app amendment two lines above · verify: `tests/unit/test_docs_auth_scope.py::test_non_google_exclusion_is_narrowed`
- [x] G0.2 · §2 B2/B3/B4 · — · `ONBOARDING:60`'s "without touching call sites" is **false** (§0.2); `:34`/`:41` gain `password_hash` and D3; `:317` Q3 is answered by this spec · verify: `tests/unit/test_docs_auth_scope.py::test_onboarding_prd_matches_the_build`

### [x] G1 — Step 1: the KDF — *dep: G0*
- [x] G1.1 · §6 Step 1 · A1 · `hash_password` / `verify_password` — scrypt from the **declared** `cryptography` (D4), no new dependency · verify: `tests/unit/test_passwords.py::test_round_trip`
- [x] G1.2 · §6 Step 1 · A2 · **G-kdf, the definition of done** — the stored value is `scrypt$n$r$p$salt$hash`, salted, and contains no plaintext. **Two hashes of the same password must differ**, or the salt is not doing its job · verify: `tests/unit/test_passwords.py::test_hash_format`
- [x] G1.3 · §6 Step 1 · A3 · verifying against a **null** hash does the KDF work anyway (D9) — an early return makes the login form an account-existence oracle · verify: `tests/unit/test_passwords.py::test_no_user_enumeration_by_timing`

> **G1 landed.** 12 tests. **All three G-kdf checks mutation-verified** — sha256 substituted for
> scrypt, a constant salt, and an early return on a null hash each turn exactly one intended
> test red, and the source restores byte-for-byte. The gates have teeth; that check is the
> whole reason to trust them, since every mutation above passes `test_round_trip` unharmed.

### [x] G2 — Step 2: the schema — *dep: G1 — MUST precede G3*
- [x] G2.1 · §6 Step 2 · A4 · `0017` — `google_sub` nullable, `password_hash`, `password_set_at`, the reset table; own engine, real Alembic up→down→up. **`test_membership.py:95` inverts and `test_pg_baseline.py:149` goes 56→57 in this commit** · verify: `tests/integration/test_migration_password_auth.py::test_up_down`
- [x] G2.2 · §6 Step 2 · A5 · **D3's partial index** — two password users cannot share a case-folded email; **two Google users still can**. The second half is what proves the index is partial rather than table-wide · verify: `tests/integration/test_migration_password_auth.py::test_partial_unique_index`

> **G2 landed.** 3 tests, and all three §0.5 gates edited in this same commit as required —
> `test_membership.py:95` inverted (with the reason), `test_pg_baseline.py` 56→57,
> `password_reset_tokens` added to `GLOBAL_TABLES`.
>
> **G-partial mutation-verified.** A table-wide unique instead of a partial one — the exact
> design error A5 exists to catch, and the one that passes half the criterion — turns
> `test_partial_unique_index` red. So does dropping `lower()`, and so does leaving `google_sub`
> NOT NULL. Migration and model restore byte-for-byte.
>
> One correction found by running it: Postgres renders the expression as `lower((email)::text)`,
> so the first version of the DDL assertion failed against a correct index. The assertion was
> wrong, not the schema.

### [x] G3 — Step 3: signup and login — *dep: G2 — MUST precede G5*
- [x] G3.1 · §6 Step 3 · A6 · `/signup` creates a user and routes to `/onboarding/`; `login.html` gains the form and a real `/signup` link — **the "New here?" line finally has a destination** · verify: `tests/integration/test_password_auth.py::test_signup_creates_user`
- [x] G3.2 · §6 Step 3 · A7 · a wrong password is refused, creates **no session**, and does not reveal whether the email exists (D9/N5) · verify: `tests/integration/test_password_auth.py::test_wrong_password_refused`
- [x] G3.3 · §6 Step 3 · A8 · sign-in **rotates** the session id — the same fixation defence `routes/auth.py:176` already applies to the OIDC path · verify: `tests/integration/test_password_auth.py::test_signin_rotates_session`

> **G3 landed.** 9 tests. **All four G-oracle mutations caught** — a helpful "no account with
> that email", an early return on the unknown-email path, no session rotation, and a session
> minted before verification. Each is the natural thing to write; each turns its own gate red.
>
> **The timing mutation is the one that matters.** G1 proved `verify_password(x, None)` derives
> anyway; that is a claim about the *function*. The route short-circuiting above it is a
> different defect, invisible in every response, and only
> `test_login_costs_the_same_whether_the_email_exists` — which counts KDF invocations at the
> route — sees it.
>
> **`auth/session_flow.py` is new and not in the spec.** §4/§5 do not name it. The OIDC callback
> already contained rotation, cookie flags and the `/` vs `/onboarding/` choice, all of which a
> password login needs verbatim and none of which is about Google. Copying them would have left
> two implementations of session rotation — the kind of thing fixed once and then not again in
> the copy. `routes/auth.py` now calls it, so both paths share one definition.
>
> **`test_route_declarations.py:90-93` retired** (§0.5's correctness-debt item): the `auth`
> mechanism string said *"the OIDC provider's own flow"*, which became false the moment a second
> credential type could mint a session. Nothing failed automatically — the test only
> length-checks the prose — so it was corrected deliberately rather than left.
>
> Two SPEC-009 gates fired as predicted: `signup.html` needed `npm run build:css && npm run
> stamp` (A13) and a checklist row (A11). `login.html`'s row went 79 → 124 lines.
>
> One test I wrote asserted `GET /login` returns 200. It returns **401**, deliberately —
> `routes/auth.py:94` makes the login page *itself* the unauthenticated response. My assertion
> was wrong, not the route.

**`PENDING_TESTS_IN_EXISTING_FILES`** — `tests/integration/test_password_auth.py::test_invitee_without_google`
(A14) does not exist yet: §8 groups criteria by file, and G6 writes into the file G3 created.
**Delete this entry when G6 lands.**

### [x] G4 — Step 4: rate limiting — *dep: G3*
- [x] G4.1 · §6 Step 4 · A9 · **per-email AND per-IP** (D7) — either alone has a trivial bypass: per-email lets a botnet spread one guess per host, per-IP lets one host walk a user list · verify: `tests/unit/test_login_ratelimit.py::test_throttled_by_email_and_ip`
- [x] G4.2 · §6 Step 4 · A10 · a successful sign-in clears the counter, or a user who mistypes twice stays locked out · verify: `tests/unit/test_login_ratelimit.py::test_success_clears_attempts`

> **G4 landed on the second attempt.** The first (`1b359bf`) was reverted in `cba4dd4`: it did
> not run — `check()` required a `kind` argument both call sites omitted, so every request to
> `/login` raised TypeError and all 8 G3 tests failed. It was also built against a different
> design from §5.2 (a class with a `kind` discriminator, no `clear_attempts` at all, so A10 was
> unsatisfiable at any threshold) and wrote `test_auth_ratelimit.py` rather than the
> `test_login_ratelimit.py` node ids A9/A10 name — which is why the reconciler never flagged it
> and still read 8/15.
>
> **All 8 mutations caught**, including both halves of D7 independently: removing the per-email
> limit and removing the per-IP limit each turn A9 red on their own, and so does collapsing the
> two into one `(email|ip)` counter. That triple is what proves the limits are separate rather
> than one limiter satisfying both assertions.
>
> **The oracle mutation took three attempts to catch, and the reason is the finding.** "Bank
> failures only for addresses that exist" stayed green against both a status-code comparison and
> a strengthened shared-counter version. A probe showed why: a throttled request and an ordinary
> wrong password both return `_signin_failed` at 401 **by design**, so no comparison of status or
> body can ever separate them. The asymmetry is only observable in **cost** — a throttled request
> skips the KDF — so the assertion counts `_derive` invocations, the same instrument A7 uses.
> A test that cannot fail is worse than no test; two rounds of mutation testing are what surfaced
> that this one couldn't.
>
> **`db` is accepted and unused** in all three functions, deviating from §5.2's implied table.
> U5 states the limiter is in-process and is the authority; the parameter stays so call sites
> survive the move to a shared store. Recorded in the module docstring, not hidden.
>
> `test_password_auth.py` gains an autouse `reset_all()` fixture — the counters are module state,
> so without it one test's failures throttle the next and the suite passes on ordering.

### [x] G5 — Step 5: password reset — *dep: G3*
- [x] G5.1 · §6 Step 5 · A11 · the request response is **identical** for a known and an unknown address (N5) · verify: `tests/integration/test_password_reset.py::test_no_enumeration_on_request`
- [x] G5.2 · §6 Step 5 · A12 · the token is single-use and expires — the existing sha256 pattern verbatim, **not** the KDF (§0.6 of the spec) · verify: `tests/integration/test_password_reset.py::test_token_single_use_and_expiry`
- [x] G5.3 · §6 Step 5 · A13 · **completing a reset revokes every existing session.** The spec flags this as the criterion most likely to be forgotten: a reset that leaves old sessions alive does not lock out the attacker the user is resetting because of. Mail is `transactional`, never `lifecycle` (D8/N8) · verify: `tests/integration/test_password_reset.py::test_reset_revokes_all_sessions`

> **G5 landed.** 10 tests, **all 8 mutations caught first time** — including the two with no
> natural gate.
>
> **A13 was built first**, on the reasoning that its failure is invisible: the reset demos
> perfectly without it. Asserted as a *count* of surviving sessions (three minted, one expected —
> the reset's own), plus a bystander whose session must survive. "The old cookie stopped working"
> would pass with two of three sessions still live.
>
> **D8 has no behavioural gate and needed a source assertion.** `lifecycle` mail is
> suppression-checked and returns silently, so an unsubscribed user would be permanently locked
> out with no error anywhere — and the console provider used in tests does not consult the
> suppression list, so every functional test passes either way. Asserted by parsing the `klass`
> argument, with a mutation check proving the scan can see a `lifecycle` value.
>
> **Two findings from building it, both real:**
>
> 1. **A misconfigured mail provider was an oracle.** `get_email_provider` raises when
>    `RESEND_API_KEY` is unset — the default — so an uncaught raise meant a **500 for a real
>    address and a 200 for an unknown one**. A server that is merely misconfigured handed out the
>    account list. Now caught and logged; the token stays minted so a later resend works.
> 2. **My outbox assertion was wrong about the architecture.** I asserted a queued row, which
>    failed against correct code: `_send` enqueues only when an account is bound, and a reset
>    happens *before sign-in*, so it goes inline — the same carve-out the waitlist confirmation
>    holds, because an outbox row with no owner could never be drained under RLS. Re-asserted at
>    the provider boundary, where both paths converge.
>
> **Step 4's second half landed here.** §6 says the limiter is wired into "login **and** reset";
> G4 wired only login. Unthrottled, `/password/reset` mails a stranger on demand — a mail bomb
> pointed at any address an attacker names. The throttled reply is byte-identical to an ordinary
> one, or the throttle re-answers the question A11 refuses.

### [ ] G6 — Step 6: the invite path — *dep: G3*
- [ ] G6.1 · §6 Step 6 · A14 · `ONBOARDING:317` Q3, answered — an invitee with no Google account accepts by signing up with a password · verify: `tests/integration/test_password_auth.py::test_invitee_without_google`

### [ ] G7 — the exit criterion — *dep: all*
- [ ] G7.1 · §6 exit · A15 · **end to end** — signup → onboarding → sign out → sign in → reset → sign in with the new password · verify: `tests/integration/test_password_e2e.py::test_exit_criterion`

### [ ] G-Final — Compound-stop verification
- [ ] F.1 · full suite green — baseline **2707**; a new skip is red
- [ ] F.2 · every criterion green by its own node id — **all 15**
- [ ] F.3a · every §6 step tasked — **6 steps**
- [ ] F.3b · `--collect` exits 0, `PENDING_TESTS_IN_EXISTING_FILES` **empty**
- [ ] F.4 · smoke green
- [ ] F.5 · write `tasks/build-loop-spec010-report.md`

---

## 2. Group-specific gates

| Group | Gate | Failure class |
|---|---|---|
| **G1** | A2 asserts the **format and the salt**, not just that verification works | A sha256 hash verifies perfectly and fails only on a leak |
| **G2** | A5's second half — **two Google users may still share an email** | Proves the index is partial. A table-wide unique would pass the first half and break Google identity |
| **G3** | A7 asserts **no session row was created**, not merely a non-200 | A refused login that still mints a session is a working bypass |
| **G4** | A9 exercises the two limits **independently** | One limiter satisfying both assertions is the bypass D7 names |
| **G5** | A13 asserts a **count of zero** surviving sessions | "The new password works" is true whether or not old sessions died |

---

## 3. Gates this spec cannot close by itself

| Gate | Check | Closes |
|---|---|---|
| **G-kdf** | Assert the stored prefix is `scrypt$`, that two hashes of one password **differ**, and that no `sha256` call appears in `passwords.py` | **A2** — the adjacent code comments recommend exactly the wrong primitive, so this is the likeliest defect in the spec |
| **G-oracle** | A3/A7/A11 assert **response equality** between the known and unknown cases — same status, same body, same redirect | **A3/A7/A11** — a helpful error message is the defect, and it is the natural thing to write |
| **G-revoke** | A13 counts rows in `sessions` for that user after the reset | **A13** — the feature appears to work regardless |
| **G-partial** | A5 asserts both halves | **A5** — half of it passes on a table-wide unique index |

---

## 4. Recurring hazards, pre-declared

**`PENDING_TESTS_IN_EXISTING_FILES` will be needed at least twice.** §8 groups by **file**:
`test_password_auth.py` holds A6/A7/A8 (G3) **and** A14 (G6); `test_passwords.py` holds A1–A3
but lands whole at G1. So G3 creates the file G6 writes into. This recurred three times in
SPEC-006 and once in SPEC-009 — add the entry, annotate it, **delete it when its group lands**.

**Every negative needs a positive twin** (§0.5b), and this spec is unusually full of them:
"refused", "no session", "does not reveal", "revokes every". Each is satisfied by a component
that does nothing. A7 must assert a *correct* password still works; A11 that mail is actually
sent for the known address; A13 that the user can sign in afterwards with the new password.

**Two SPEC-009 gates will fire on every new template.** `signup.html`,
`password_reset_request.html` and `password_reset.html` each need `npm run build:css && npm run
stamp` (A13) and a row in `docs/UI_MANUAL_CHECKLIST.md` (A11). Both fired on the login-page
commit; expect them again.

**Do not assert wall-clock timing.** A3's real check is that the *code path* runs the KDF even
for a missing user. A wall-clock comparison on a shared CI box is flaky, and a flaky security
test gets disabled — which is worse than not having written it.
