# SPEC-010 — Email/password authentication

**Phase:** 4+ — reverses a GA-scope exclusion; see §0.1
**Status:** Ready to build — **2 open decisions** (O1: whether one person may hold both a password and a Google identity; O2: the password policy)
**Written:** 2026-08-31
**Verified against:** `worktree-spec-build-harness` @ **`c9e2b77`**. Every claim below was measured at that ref — column nullability, grep counts, dependency lists, importability. **Not `origin/main`** (§0.5)
**Source PRDs:** `../product/ONBOARDING_AUTH_RBAC.md` — the document that **owns** authentication (§3, §11 Q3). `../product/SAAS_PRD.md` §6.2 — which says the opposite; see §0.1
**Depends on:** SPEC-002 (users, sessions, the global-table carve-out), SPEC-003 (memberships, RBAC, the invite flow), SPEC-005 (`EmailProvider`, for reset mail)

**Goal.** Someone with no Google account can create a MiHomes account, sign in, and recover
access if they forget their password.

**Exit criterion:** a new user signs up with an email and password, is routed through onboarding,
signs out, signs back in, resets a forgotten password by email, and signs in with the new one —
with every credential stored as a salted KDF hash and every failure path rate-limited.

**The stake.** Every other spec in this set moves data behind an already-solved front door.
**This one builds the door.** A password bug is not a degraded feature — it is unauthorised
access to an entire estate's records, staff roster and financials, and the failure is silent:
the attacker looks exactly like the owner. `sessions.py:9` already states the codebase's
position on this — *"No salt and no bcrypt, deliberately"* — and that reasoning **inverts** here.
A session id is 256 bits of `secrets` output with no dictionary to attack. A password is
human-chosen and appears in every leaked-credential list on the internet.

---

## 0. Six things a reader must know before trusting this spec

### 0.1 — The two PRDs contradict each other, and this spec picks one

Measured, both documents at `c9e2b77`:

- **`SAAS_PRD.md:105`** — *"Non-Google auth; marketing automation; multi-language"* under
  **explicitly out of scope for initial GA**.
- **`ONBOARDING_AUTH_RBAC.md:11`** — *"This document **owns**: Google authentication, first-run
  onboarding, staff/teammate invitations…"* — and at **`:60`**: *"The auth layer is abstracted
  behind an `IdentityProvider` interface **so email/password or additional IdPs can be added
  later without touching call sites**."*

The owning document planned for this; the scope list forbids it. **This spec resolves in favour
of the owning document** (D1), and §2's doc-fix amends `SAAS_PRD:105` the way SPEC-009 §2 B1
amended the native-app line beside it — the exclusion is narrowed, not deleted.

**That is a founder-level reversal and it is recorded as one.** If the intent was genuinely
Google-only forever, stop here: the honest outcome is to strike `ONBOARDING:60`'s promise
instead, and this spec should not be built.

### 0.2 — The abstraction that was supposed to make this free does not fit

`ONBOARDING:60` promises email/password lands *"without touching call sites"*. Measured against
the real Protocol at `auth/oidc.py:55-65`:

```python
class IdentityProvider(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...
    def exchange_code(self, *, code: str, code_verifier: str) -> str: ...
    def verify(self, id_token: str) -> IdentityClaims: ...
```

**All three methods are OAuth-shaped.** A password login has no authorization URL, no
authorization code, and no ID token. A `PasswordProvider` implementing this Protocol would have
to raise `NotImplementedError` from every method and expose its real behaviour some other way —
which is not an implementation of the Protocol, it is a lie about one.

**So the promise does not hold, and this spec does not pretend otherwise (D2).** Password auth
is a **second, parallel entry path** that converges on the shared `create_session(db, user_id)`.
That convergence is the real reusable seam, and it is genuinely method-agnostic: `sessions.py`
imports nothing from `oidc.py`, and `create_session` takes only a `user_id` (§0.4).

### 0.3 — `users.email` is deliberately NOT unique, and that is the hardest constraint

Not `google_sub`. Measured:

- `models/user.py:31` — `email` is `String(320)`, **`index=True`, not unique**, commented
  *"display only, may change"*.
- `0001_pg_baseline.py:107` — `ix_users_email ... unique=False`.
- **Two tests assert this on purpose.** `test_membership.py:103` —
  `assert email.unique is not True, "email must NOT be the unique identity key"`. And
  `test_auth.py:283` asserts *the same email under a different sub is a different person*.
- The reasoning is written down at `test_membership.py:97-100`: *"Keying on email would break
  the moment someone changes their Google address, silently orphaning their memberships."*

**Password authentication has no identifier but the email.** There is no `sub`. So this spec
must make email unique — for password users at least — against a schema that was deliberately
designed the other way, with tests defending the decision.

**D3 resolves it with a partial unique index**, not a table-wide one:

```sql
CREATE UNIQUE INDEX uq_users_email_password
    ON users (lower(email)) WHERE password_hash IS NOT NULL;
```

Google identity stays keyed on `sub` and keeps its non-unique email; a password user's email is
unique among password users. `lower(email)` because `Alice@x.com` and `alice@x.com` are the same
person to everyone except a byte comparison, and two accounts one keystroke apart is an account
-takeover vector rather than a cosmetic flaw.

**`test_membership.py:103` still passes** — the *column* is still not unique; the constraint
lives in a partial index. That is not a loophole, it is the distinction the test was written to
protect: email is not the identity key for Google users, and after this it still is not.

### 0.4 — What is already correct, and must not be rebuilt

The survey measured these as reusable **as-is**:

| Component | Why it holds |
|---|---|
| `create_session(db, user_id)` (`sessions.py:96`) | Takes a user id and nothing else. Imports nothing from `oidc.py`. **Already method-agnostic** — this is the seam D2 relies on |
| `lookup_session` / `revoke_session` / `revoke_all_sessions` | No knowledge of how the user authenticated |
| The cookie flags (`routes/auth.py:75-93`) | `httpOnly`, `SameSite=Lax`, `Secure` off loopback only — decided from the request host, not a debug flag |
| Session rotation on sign-in | `routes/auth.py:176-179` already revokes a pre-existing session; the password path must do the same |
| CSRF (`auth/csrf.py`) | `compare_digest`, rejects blanks on either side |
| The single-use token pattern | Five instances, all `sha256` hex in a `String(64)` column, raw returned once, `expires_at` compared tz-normalized. **Directly reusable for the reset token** (§0.6) |

### 0.5 — The tree this is verified against is not canon

`worktree-spec-build-harness` is **~200 commits ahead of `origin/main`, 0 behind**. The merge is
a clean fast-forward and is a human action; it has been offered repeatedly and not taken. A
future reader must not read "SPEC-010 built" as "main has it". Carried as **U1**.

### 0.6 — The codebase's own hashing rule inverts here, and the spec says so twice

`sessions.py:9-12`: *"No salt and no bcrypt, deliberately. A session id is 256 bits of `secrets`
output, not a human-chosen password: there is no dictionary to attack, so a slow KDF buys
nothing."* `invite_service.py:62-64` repeats it.

**That reasoning is correct for tokens and exactly wrong for passwords.** So this spec splits
the two, and the split is a criterion rather than a convention (A2/A6):

- **The password** → salted, slow KDF. Never sha256.
- **The reset token** → the existing pattern verbatim: `secrets.token_urlsafe(32)`, sha256 hex in
  `String(64)`, raw returned once, single-use, short expiry.

Getting this backwards in either direction is a real defect: a sha256 password is crackable
offline at billions of guesses per second, and a scrypt-hashed reset token would make every
verification a deliberate CPU burn on an unauthenticated endpoint — a denial-of-service tool
handed to an attacker.

---

## 1. Decisions

### 1.1 Locked

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Email/password ships. `ONBOARDING:60` wins over `SAAS_PRD:105`** | The owning document planned for it (§0.1). Recorded as a founder reversal, with §2's doc-fix narrowing the exclusion rather than deleting it |
| **D2** | **Password auth is a parallel entry path, not an `IdentityProvider` implementation** | §0.2 — the Protocol is OAuth-shaped and a password login can implement none of its three methods. Both paths converge on `create_session`, which is the seam that actually generalises |
| **D3** | **A partial unique index on `lower(email) WHERE password_hash IS NOT NULL`** | §0.3. Google identity stays keyed on `sub`; email uniqueness applies only where it is the identity. Case-folded, because two accounts one keystroke apart is a takeover vector |
| **D4** | **`scrypt` from the already-declared `cryptography`** — n=2^15, r=8, p=1, 16-byte salt, 32-byte output | Measured: `cryptography 46.0.6` is a declared runtime dependency (`pyproject.toml:33`) and `Scrypt` imports and derives correctly here. **No new dependency.** argon2id is the modern default and would mean adding `argon2-cffi`; scrypt is memory-hard, in the standard library's reach, and already audited as part of a dependency this project ships |
| **D5** | **Hashes are stored self-describing**: `scrypt$n$r$p$<salt_b64>$<hash_b64>` | Mirrors `crypto.py:52`'s `enc:v1:` prefix convention. Parameters travel with the hash, so raising `n` later re-hashes on next login instead of locking everyone out |
| **D6** | **`google_sub` becomes nullable; `password_hash` is nullable** | A user has one, the other, or (per O1) both. Neither is universally present, so neither can be NOT NULL |
| **D7** | **Rate limiting is required, not optional** — per-email **and** per-IP | The main app has **none** (§0.7). A login endpoint without it is an offline-speed online attack. Per-email alone lets a botnet spread by IP; per-IP alone lets one attacker walk a user list from one host |
| **D8** | **Reset mail is `transactional`, never `lifecycle`** | Measured at `services/email/service.py:74-86`: `lifecycle` mail is suppression-checked. A user who unsubscribed and then forgot their password would be **permanently locked out**, and the failure would look like the reset feature being broken |
| **D9** | **Timing is constant across "no such user" and "wrong password"** | Otherwise the login form is a user-enumeration oracle: an attacker learns which emails hold accounts, which is the first step of a credential-stuffing run |

### 1.2 `OPEN — needs decision: founder`

| # | Question | Blocks |
|---|---|---|
| **O1** | **May one person hold both a password and a Google identity for the same email?** Allowing it needs a linking flow with verification — `ONBOARDING:314` Q1 already defers exactly this. Forbidding it means someone who signed up with a password and later clicks "Sign in with Google" gets a confusing refusal | **Blocks the collision path only.** Build refusing the collision with a clear message; linking is additive. Carried as **U2** |
| **O2** | **The password policy.** Minimum length, and whether to check against a breached-password list | **Blocks nothing.** Build with a documented default (12 characters, no composition rules — length beats character classes, and composition rules push people toward `Password1!`). Carried as **U3** |

---

## 2. Doc-fix prerequisites

| # | Doc + location | Fix |
|---|---|---|
| **B1** | `SAAS_PRD.md:105` — *"Non-Google auth"* under out-of-scope | **Narrow, do not delete.** Email/password ships (D1); what stays out is *additional third-party IdPs* — Apple, Microsoft, SAML. Same shape as SPEC-009 §2 B1's native-app amendment two lines above it, and for the same reason: a bare exclusion read as a blanket ban on something the owning PRD had planned for |
| **B2** | `ONBOARDING_AUTH_RBAC.md:60` — *"…added later **without touching call sites**"* | **Correct the claim.** Measured (§0.2): the Protocol's three methods are all OAuth-shaped and a password login implements none of them. Say that the shared seam is `create_session`, not `IdentityProvider` — a promise a future reader would otherwise plan against |
| **B3** | `ONBOARDING_AUTH_RBAC.md:34, :41` — the `users` table description and *"Identity is keyed on Google `sub`, not email"* | Add `password_hash` to the column list, and record D3: `sub` remains the key **for Google identities**; a password identity is keyed on case-folded email, enforced by a partial unique index |
| **B4** | `ONBOARDING_AUTH_RBAC.md:317` — Q3, *"if we add email/password before an invitee has Google, how does acceptance work across IdPs?"* | **This spec answers it.** An invitee follows the same signup form; `accept_invite` already keys on the invite token, not on the identity method. §6 Step 6 verifies it |

---

## 3. File manifest

### New

```
src/mihomes/auth/passwords.py              hash_password / verify_password / needs_rehash (D4/D5)
src/mihomes/auth/ratelimit.py              per-email + per-IP attempt limiting (D7)
src/mihomes/models/password_reset.py       PasswordResetToken — global, like users and sessions
alembic/versions/0017_password_auth.py     google_sub nullable, password columns, partial index, reset table
src/mihomes/web/routes/password.py         GET/POST /signup, POST /login, the reset pair
src/mihomes/web/templates/signup.html      the form login.html's "New here?" line will finally link to
src/mihomes/web/templates/password_reset_request.html
src/mihomes/web/templates/password_reset.html
src/mihomes/services/email/templates/password_reset.html + .txt   ** both, or render raises **
```

### Modified

| Path | Change |
|---|---|
| `src/mihomes/models/user.py` | `google_sub` nullable; `password_hash`, `password_set_at` |
| `src/mihomes/web/templates/login.html` | An email/password form beside the Google button; "New here?" becomes a real link to `/signup` |
| `src/mihomes/services/email/service.py` | `send_password_reset(...)`, `klass="transactional"` (D8) |
| `tests/unit/test_membership.py:95` | `google_sub.nullable` assertion inverts — **in the same commit as the migration**, with the reason |
| `tests/unit/test_route_declarations.py` | `PERMANENT_ALLOWLIST` + `ALLOWLIST_MECHANISMS` for `web.routes.password`; **and `auth`'s existing mechanism string, which says "the OIDC provider's own flow" and stops being true** |
| `tests/integration/test_pg_baseline.py:149` | table count **56 → 57** |
| `pyproject.toml` | **nothing** — D4 uses a declared dependency |

---

## 4. Schemas as code

### 4.1 `users` — three changes

```python
# google_sub: NOT NULL -> nullable. A password user has no subject (D6).
google_sub: Mapped[str | None] = mapped_column(
    String(255), unique=True, nullable=True, index=True
)

# The credential. `scrypt$n$r$p$salt$hash` (D5) — parameters travel with the value so a
# later cost increase re-hashes on next login rather than invalidating every password.
password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

# When it was last set. Not decorative: it is what a "your password was changed" notice and
# any future rotation policy read, and it distinguishes "never had one" from "set long ago".
password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

**A nullable unique column is correct in Postgres** — multiple NULLs do not collide, so every
password user can have `google_sub IS NULL` while Google users stay unique.

### 4.2 The partial unique index (D3)

```python
op.execute(
    "CREATE UNIQUE INDEX uq_users_email_password "
    "ON users (lower(email)) WHERE password_hash IS NOT NULL"
)
```

Expression-based and partial, so it must be raw SQL — `op.create_index` cannot express
`lower(email)` with a `WHERE` clause portably. **`test_baseline_matches_metadata` compares
`Base.metadata` against the migrated schema**, so this index must also be declared on the model
via `Index("uq_users_email_password", func.lower(email), unique=True, postgresql_where=...)`
or the drift check fails.

### 4.3 `password_reset_tokens` — global, and the fifth instance of a settled pattern

```python
class PasswordResetToken(Base):
    """GLOBAL, like `users` and `sessions`: read before any account context exists.

    Single-use and short-lived. The raw token is returned once, to the email, and never
    stored — only `sha256(raw)`, exactly as `invites`, `sessions`, `waitlist` and
    `gateway_link_tokens` already do (§0.6).
    """
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

**`GLOBAL_TABLES`, not `TENANT_TABLES`** — a reset happens before sign-in, so there is no
account to scope to. Same carve-out `users` and `sessions` already hold. **No RLS policy**, and
`test_pg_baseline.py:149` goes 56 → 57 in the same commit, per that file's own rule that raising
the count is a recorded decision rather than a silent adjustment.

**TTL: 1 hour.** Shorter than the invite's 7 days because a reset link is a live credential for
a *specific existing account*, and it lands in an inbox that may itself be compromised.

---

## 5. Function signatures

### 5.1 `auth/passwords.py`

```python
SCRYPT_N = 2**15   # ~32 MiB per hash. The cost knob; raising it is a D5 re-hash, not a reset.
SCRYPT_R = 8
SCRYPT_P = 1

def hash_password(plain: str) -> str:
    """`scrypt$n$r$p$<salt_b64>$<hash_b64>` (D4/D5). Fresh 16-byte salt per call."""

def verify_password(plain: str, stored: str | None) -> bool:
    """Constant-time compare. **`None` still does the work.**

    A user with no password must cost the same as one with a wrong password, or the response
    time answers "does this account exist?" — D9. So a `None` stored hash verifies against a
    fixed dummy hash and returns False, rather than returning early.
    """

def needs_rehash(stored: str) -> bool:
    """True when the stored parameters are weaker than the current constants (D5)."""
```

### 5.2 `auth/ratelimit.py` (D7)

```python
def check_login_attempt(db, *, email: str, ip: str) -> None:
    """Raise `TooManyAttempts` when either bucket is exhausted.

    **Both, not either.** Per-email alone lets a botnet spread one guess per host; per-IP alone
    lets one host walk a user list. The landing app's `TokenBucket` is in-process and per-IP
    only (`landing/ratelimit.py:57`) — reusable in shape, not in instance.
    """

def record_failure(db, *, email: str, ip: str) -> None: ...
def clear_attempts(db, *, email: str) -> None:
    """On success. Otherwise a legitimate user who mistypes twice stays throttled."""
```

### 5.3 `web/routes/password.py`

```python
@router.get("/signup")           # the form
@router.post("/signup")          # create user + password, then -> /onboarding/
@router.post("/login")           # verify, rotate session, -> / or /onboarding/
@router.get("/password/reset")   # request form
@router.post("/password/reset")  # mint token, send mail, ALWAYS the same response (A7)
@router.get("/password/reset/{token}")   # the form, token validated before render
@router.post("/password/reset/{token}")  # set the new password, revoke every session
```

---

## 6. Sequenced steps

**Step 1 — the KDF.** `passwords.py`, no routes. *Verify:* a hash round-trips (A1); the stored
format is `scrypt$…` and never contains the plaintext (A2); `verify_password(x, None)` returns
False **and takes comparable time** to a real failure (A3).

**Step 2 — the schema.** `0017`, the model changes, the partial index, the reset table.
**Before Step 3.** *Verify:* the migration applies and reverts (A4); two password users cannot
share a case-folded email, while two Google users still can (A5).

**Step 3 — signup and login.** The routes and templates; `login.html` gains the form and the
`/signup` link. **After Step 2.** *Verify:* signup creates a user and routes to onboarding (A6);
a wrong password is refused with no session created (A7); sign-in rotates the session id (A8).

**Step 4 — rate limiting.** `ratelimit.py`, wired into login and reset. *Verify:* repeated
failures are refused by email and by IP independently (A9); success clears the counter (A10).

**Step 5 — password reset.** The token, the mail, the four routes. **After Step 3.** *Verify:*
the request response is identical for a known and an unknown address (A11); a token is
single-use and expires (A12); completing a reset **revokes every existing session** (A13).

**Step 6 — the invite path.** `ONBOARDING:317` Q3. *Verify:* an invitee with no Google account
can accept an invite by signing up with a password (A14).

**Exit criterion check.** Steps 1–6 green: signup → onboarding → sign out → sign in → forget →
reset → sign in with the new password. That is A15.

---

## 7. Non-goals

**N1 — Do not implement `IdentityProvider`.** D2/§0.2. A class that raises
`NotImplementedError` from all three methods is not conformance, and the next reader will
believe the Protocol means something it does not.

**N2 — Do not make `users.email` unique table-wide.** D3. It breaks
`test_membership.py:103` and `test_auth.py:283`, both of which assert a *correct* property of
Google identity, and the migration would fail on any pre-existing duplicate.

**N3 — Do not hash passwords with sha256.** §0.6. The codebase's own comments recommend it for
tokens; that reasoning does not transfer, and a reviewer copying the adjacent pattern is the
most likely way this goes wrong.

**N4 — Do not add a second session mechanism.** Both paths call `create_session`. That is the
one thing `ONBOARDING:60`'s promise got right.

**N5 — Do not reveal whether an email exists**, on login *or* on reset request. D9/A7/A11.

**N6 — Do not skip rate limiting because it is "not the interesting part".** D7. It is the
difference between a password login and a password oracle.

**N7 — Do not build account linking.** O1/U2. It needs a verification flow of its own, and
`ONBOARDING:314` already defers it.

**N8 — Do not send reset mail as `lifecycle`.** D8 — a suppressed recipient would be locked out
permanently, and the symptom looks like a broken feature rather than a policy decision.

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | A password hashes and verifies; a wrong one does not | `test_passwords.py::test_round_trip` |
| A2 | **The stored value is `scrypt$…`, salted, and contains no plaintext** | `test_passwords.py::test_hash_format` |
| A3 | **Verifying against a null hash costs the same as a wrong password** (D9) | `test_passwords.py::test_no_user_enumeration_by_timing` |
| A4 | The migration applies and reverts cleanly | `test_migration_password_auth.py::test_up_down` |
| A5 | **Two password users cannot share a case-folded email; two Google users still can** | `test_migration_password_auth.py::test_partial_unique_index` |
| A6 | Signup creates a user and routes to onboarding | `test_password_auth.py::test_signup_creates_user` |
| A7 | **A wrong password is refused, creates no session, and does not reveal whether the email exists** | `test_password_auth.py::test_wrong_password_refused` |
| A8 | Sign-in rotates the session id | `test_password_auth.py::test_signin_rotates_session` |
| A9 | **Repeated failures are throttled by email and by IP, independently** | `test_login_ratelimit.py::test_throttled_by_email_and_ip` |
| A10 | A successful sign-in clears the counter | `test_login_ratelimit.py::test_success_clears_attempts` |
| A11 | **The reset request response is identical for known and unknown addresses** | `test_password_reset.py::test_no_enumeration_on_request` |
| A12 | A reset token is single-use and expires | `test_password_reset.py::test_token_single_use_and_expiry` |
| A13 | **Completing a reset revokes every existing session** | `test_password_reset.py::test_reset_revokes_all_sessions` |
| A14 | An invitee with no Google account can accept by signing up | `test_password_auth.py::test_invitee_without_google` |
| A15 | **End to end: signup → onboarding → sign out → sign in → reset → sign in again** | `test_password_e2e.py::test_exit_criterion` |

**A2 is the phase's definition of done.**

> **A password stored badly is indistinguishable from one stored well, until a database leaks.**
> Both are opaque strings in a `String(255)` column; both let the right password in and the wrong
> one out; every functional test passes either way. The only difference is what an attacker can
> do with a stolen copy — and by the time that matters, every user's password is already gone.

This is the research analogue of SPEC-008's A5 and the tenancy analogue of SPEC-006's A11: the
criterion that cannot be verified by using the feature.

**A13 is the one most likely to be forgotten.** A reset that leaves old sessions alive does not
lock the attacker out — which is the entire reason the user is resetting.

**A15 is the exit criterion.** If A15 is red the stage has not shipped.

---

## 9. Test manifest

```
tests/unit/test_passwords.py                 A1-A3 — the KDF, in isolation
tests/unit/test_login_ratelimit.py           A9, A10
tests/integration/test_migration_password_auth.py  A4, A5 — own engine, real Alembic
tests/integration/test_password_auth.py      A6-A8, A14
tests/integration/test_password_reset.py     A11-A13
tests/integration/test_password_e2e.py       A15 — the exit criterion
```

**Follow `test_auth.py`'s conventions**, which the survey measured as deliberate:

- **Fake at the seam, never by patching internals.** `_FakeProvider` (`test_auth.py:41-66`)
  implements the Protocol; the `_provider()` indirection exists for it. The password path's
  equivalent seam is the email provider — use `console`, not a mocked `resend`.
- **Assert raw `set-cookie` headers, not the jar** (`test_auth.py:291`). Cookie *flags* are the
  criterion; a jar normalises them away.
- **Core inserts for `TenantOwned` fixtures** (`_grant`, `:126-144`) — the ORM path demands the
  account context auth runs before.
- **Never leave a security assertion behind a conditional skip.** `test_auth.py:318`'s docstring
  records that an earlier version ended in `pytest.skip` and calls it *"a red gate"*.
- **A13 asserts a count, not a flag** — every prior session row is gone, in the manner of
  `test_signout_everywhere_ends_every_session` (`:476`).

**A3's timing assertion needs care.** A wall-clock comparison on a shared CI box is flaky. Assert
the *code path* instead — that `verify_password` performs a KDF derivation even when `stored` is
`None` — and note the wall-clock check as a manual verification. A flaky security test gets
disabled, and a disabled security test is worse than none.

---

## 10. What this stage does not make safe

- **It does not add MFA.** A stolen password is full access. That is a real gap and a separate
  spec.
- **It does not check passwords against breach corpora** (O2/U3). "hunter2" will be accepted if
  it is 12 characters.
- **It does not solve account linking** (O1/U2). One person, two identities, no merge.
- **It does not survive a database leak on its own.** D4's scrypt parameters raise the cost of
  offline cracking; they do not make it impossible, and short passwords will still fall.
- **The rate limiter is in-process.** Like `landing/ratelimit.py`, it does not coordinate across
  machines — on multiple instances the effective limit multiplies by instance count. Adequate
  single-instance; a shared store is needed before scaling out.
