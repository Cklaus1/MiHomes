"""G3 · SPEC-010 §6 Step 3 — signup, login, session rotation (A6, A7, A8).

**A7 is not a status-code test, and writing it as one is the defect.** The harness's G-oracle
gate requires *response equality* between the unknown-email and wrong-password cases — same
status, same body — plus equal cost, because the response can match perfectly while the timing
still answers "does this account exist?".

So `test_login_does_not_reveal_whether_an_email_exists` asserts the two responses are equal to
each other rather than each equal to a hardcoded 401, and
`test_login_costs_the_same_whether_the_email_exists` counts KDF invocations at the *route*
level. G1 proved `verify_password(x, None)` derives anyway; this proves the route actually
calls it instead of short-circuiting on the lookup — which is a different claim, and the one
that regresses silently.

Never wall-clock timing (harness §4): flaky on a shared box, and a flaky security test gets
disabled.

This file also holds **A14** (G6, the invite path). Until that group lands its entry sits in
`PENDING_TESTS_IN_EXISTING_FILES` — §8 groups criteria by file, and G3 creates the file G6
writes into.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from mihomes.auth import passwords as pw_module
from mihomes.auth.password_identity import MIN_PASSWORD_LENGTH, create_password_user
from mihomes.auth.sessions import SESSION_COOKIE, hash_session_id
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User
from mihomes.web.app import create_app
from mihomes.web.deps import get_db

GOOD_PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture
def client(_pg_engine):
    """Same construction as `test_auth.py`'s: a savepoint-scoped session, rolled back after."""
    connection = _pg_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection, future=True, join_transaction_mode="create_savepoint")

    def override_get_db():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as c:
        c._Session = Session
        yield c

    transaction.rollback()
    connection.close()


def _session_count(client, user_id) -> int:
    s = client._Session()
    try:
        return s.execute(
            select(func.count()).select_from(SessionRow).where(SessionRow.user_id == user_id)
        ).scalar_one()
    finally:
        s.close()


def _make_user(client, email: str, password: str = GOOD_PASSWORD) -> uuid.UUID:
    s = client._Session()
    try:
        user = create_password_user(s, email=email, password=password)
        s.commit()
        return user.id
    finally:
        s.close()


# ── A6 — signup ───────────────────────────────────────────────────────────────

def test_signup_creates_user(client):
    """**A6** — `/signup` creates a password user and routes to the onboarding wizard.

    Not to `/`: a brand-new user has no membership, and `/` raises 403 "No account selected"
    for exactly that state. Sending them there would make the reward for registering an error
    page.
    """
    r = client.post(
        "/signup",
        data={"email": "new@example.com", "password": GOOD_PASSWORD, "name": "New Person"},
        follow_redirects=False,
    )

    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/onboarding/", "a new user must land in the wizard"
    assert r.cookies.get(SESSION_COOKIE), "signup did not sign the new user in"

    s = client._Session()
    try:
        user = s.execute(
            select(User).where(User.email == "new@example.com")
        ).scalar_one()
        assert user.google_sub is None, "a password user must not be given a Google subject"
        assert user.password_hash is not None
        assert user.password_hash.startswith("scrypt$"), user.password_hash
        assert GOOD_PASSWORD not in user.password_hash, "the plaintext is in the stored value"
        assert user.password_set_at is not None
    finally:
        s.close()


def test_signup_refuses_a_duplicate_password_account(client):
    """The partial index, seen from the route. **Both halves of the response matter.**"""
    _make_user(client, "taken@example.com")

    r = client.post(
        "/signup",
        data={"email": "taken@example.com", "password": "another-long-passphrase"},
        follow_redirects=False,
    )
    assert r.status_code == 400, r.text
    assert "already exists" in r.text

    # Case folding: the index is on `lower(email)`, so this is the same account.
    r = client.post(
        "/signup",
        data={"email": "TAKEN@example.com", "password": "another-long-passphrase"},
        follow_redirects=False,
    )
    assert r.status_code == 400, "TAKEN@ and taken@ must be the same login"

    s = client._Session()
    try:
        n = s.execute(
            select(func.count()).select_from(User).where(func.lower(User.email) == "taken@example.com")
        ).scalar_one()
        assert n == 1, f"a duplicate row was written anyway: {n} users"
    finally:
        s.close()


def test_signup_enforces_the_length_floor_server_side(client):
    """`minlength` on the input is a convenience; the browser attribute is trivially removed."""
    short = "x" * (MIN_PASSWORD_LENGTH - 1)
    r = client.post(
        "/signup", data={"email": "short@example.com", "password": short}, follow_redirects=False
    )
    assert r.status_code == 400, r.text
    assert str(MIN_PASSWORD_LENGTH) in r.text

    s = client._Session()
    try:
        assert s.execute(
            select(User).where(User.email == "short@example.com")
        ).scalar_one_or_none() is None, "a user was created despite the refusal"
    finally:
        s.close()


# ── A7 — the refusal, and what it must not reveal ────────────────────────────

def test_wrong_password_refused(client):
    """**A7** — refused, and **no session row created**.

    The session count is the assertion that matters. A refused login that still mints a session
    is a working bypass, and every status-code assertion passes against it.
    """
    user_id = _make_user(client, "real@example.com")
    assert _session_count(client, user_id) == 0

    r = client.post(
        "/login",
        data={"email": "real@example.com", "password": "definitely-not-the-password"},
        follow_redirects=False,
    )

    assert r.status_code == 401, r.text
    assert not r.cookies.get(SESSION_COOKIE), "a session cookie was set on a failed login"
    assert _session_count(client, user_id) == 0, (
        "a session row was created for a REFUSED login — this is a complete authentication "
        "bypass, and the 401 above hides it"
    )

    # The positive twin (§0.5b): the correct password does create exactly one session.
    r = client.post(
        "/login",
        data={"email": "real@example.com", "password": GOOD_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert _session_count(client, user_id) == 1


def test_login_does_not_reveal_whether_an_email_exists(client):
    """**A7 · G-oracle** — the two failures are indistinguishable.

    Asserted as *equality between the two responses*, not as each matching a hardcoded 401. A
    per-case assertion passes when the two differ, which is precisely the defect: a helpful
    "no account with that email" is the natural thing to write and it hands an attacker a list
    of which addresses hold accounts.
    """
    _make_user(client, "known@example.com")

    known = client.post(
        "/login",
        data={"email": "known@example.com", "password": "wrong-password-here"},
        follow_redirects=False,
    )
    unknown = client.post(
        "/login",
        data={"email": "nobody-at-all@example.com", "password": "wrong-password-here"},
        follow_redirects=False,
    )

    assert known.status_code == unknown.status_code, (
        f"status differs: known={known.status_code} unknown={unknown.status_code}"
    )
    assert known.headers.get("location") == unknown.headers.get("location")

    # Bodies differ only where the submitted email is echoed into the form. Normalise that
    # and the rest must be byte-identical — including the error string.
    a = known.text.replace("known@example.com", "X")
    b = unknown.text.replace("nobody-at-all@example.com", "X")
    assert a == b, (
        "the two failure pages differ beyond the echoed address. Whatever that difference is, "
        "it tells a stranger which emails have accounts"
    )

    # And neither may name the cause.
    for body in (known.text, unknown.text):
        low = body.lower()
        for leak in ("no account", "not found", "unknown email", "no user", "incorrect password"):
            assert leak not in low, f"the page says {leak!r}, which distinguishes the two cases"


def test_login_costs_the_same_whether_the_email_exists(client, monkeypatch):
    """**A7 · D9, the half that is invisible in the response.**

    G1 proved `verify_password(x, None)` runs the KDF. This proves the **route** reaches it —
    that the endpoint does not return early on `user is None` before verification. Those are
    different claims: the second regresses the moment someone adds a reasonable-looking guard
    clause, and nothing in the response changes when it does.

    Counted, never timed.
    """
    _make_user(client, "exists@example.com")

    calls: list[str] = []
    real = pw_module._derive

    def counting(plain, salt, **kw):
        calls.append("derive")
        return real(plain, salt, **kw)

    monkeypatch.setattr(pw_module, "_derive", counting)

    calls.clear()
    client.post(
        "/login", data={"email": "exists@example.com", "password": "wrong"}, follow_redirects=False
    )
    known_cost = len(calls)

    calls.clear()
    client.post(
        "/login", data={"email": "ghost@example.com", "password": "wrong"}, follow_redirects=False
    )
    unknown_cost = len(calls)

    assert known_cost > 0, "the login route never ran the KDF at all"
    assert known_cost == unknown_cost, (
        f"the KDF ran {known_cost}x for a known email and {unknown_cost}x for an unknown one. "
        "That difference is readable off the response time — the login form is an "
        "account-existence oracle"
    )


def test_a_google_only_user_cannot_be_logged_in_with_a_password(client):
    """A Google account has `password_hash IS NULL`, and must not become password-accessible.

    Refused through the same path and with the same message as any other failure — naming this
    case would tell a stranger the address exists *and* which provider it uses.
    """
    s = client._Session()
    try:
        s.add(User(google_sub="sub-google-only", email="google@example.com"))
        s.commit()
    finally:
        s.close()

    r = client.post(
        "/login", data={"email": "google@example.com", "password": GOOD_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert not r.cookies.get(SESSION_COOKIE)

    # **The response must be identical to any other failure**, which is the assertion that
    # matters — not a keyword scan. The page legitimately says "Sign in with Google" (it offers
    # that button to everyone), so searching for the word would either fail on correct markup
    # or, once excluded, prove nothing. Comparing against an unknown address does prove it.
    other = client.post(
        "/login", data={"email": "nobody@example.com", "password": GOOD_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == other.status_code
    assert r.text.replace("google@example.com", "X") == other.text.replace("nobody@example.com", "X"), (
        "a Google-only address produces a different page from an unknown one, which tells a "
        "stranger the address exists and which provider it uses"
    )


# ── A8 — session rotation ─────────────────────────────────────────────────────

def test_signin_rotates_session(client):
    """**A8** — the session the browser arrived with is *revoked*, not merely replaced.

    `new != old` is not the assertion. A fixation attack plants a known session id before the
    victim signs in; if that id still resolves afterwards the attacker holds a live
    authenticated session, and minting a second id alongside it defends nothing. So this
    asserts the **old id no longer resolves**.
    """
    user_id = _make_user(client, "rotate@example.com")

    client.post(
        "/login", data={"email": "rotate@example.com", "password": GOOD_PASSWORD},
        follow_redirects=False,
    )
    first = client.cookies.get(SESSION_COOKIE)
    assert first

    client.post(
        "/login", data={"email": "rotate@example.com", "password": GOOD_PASSWORD},
        follow_redirects=False,
    )
    second = client.cookies.get(SESSION_COOKIE)
    assert second and second != first, "the session id did not change on re-authentication"

    s = client._Session()
    try:
        # `revoke_session` DELETES the row rather than flagging it (`sessions.py:199`): a
        # `revoked` column would need every lookup to remember to filter on it, and a deleted
        # row cannot be forgotten about. So "revoked" here means "gone".
        stale = s.execute(
            select(SessionRow).where(SessionRow.session_id_hash == hash_session_id(first))
        ).scalar_one_or_none()
        assert stale is None, (
            "the previous session id still resolves after signing in again. A planted session "
            "id survives authentication, which is session fixation"
        )

        # The positive twin: the NEW id does resolve, so rotation did not simply break login.
        fresh = s.execute(
            select(SessionRow).where(SessionRow.session_id_hash == hash_session_id(second))
        ).scalar_one_or_none()
        assert fresh is not None, "the new session id does not resolve — login is broken"
        assert fresh.user_id == user_id
    finally:
        s.close()


def test_login_page_offers_both_methods_and_a_signup_link(client):
    """The user-visible half of Step 3, and the fix to a copy bug the login page carried.

    "New here? The button above creates your account." was accurate only because `/signup` did
    not exist. Now it does, so the standard wording is restorable — and the assertion is that
    the link has a real destination, since a link with nowhere to go was the original defect.
    """
    r = client.get("/login")
    # 401, not 200, and deliberately so — `routes/auth.py:94` returns the login page *as* the
    # unauthenticated response, so a crawler or API client reads it as one. The browser renders
    # the body either way. Asserted explicitly rather than with `< 400`, because the day that
    # becomes a redirect is a day someone should look at this test.
    assert r.status_code == 401, r.status_code

    assert 'action="/login"' in r.text, "no password form on the login page"
    assert 'name="password"' in r.text
    assert "/auth/google/start" in r.text, "the Google option was removed rather than joined"

    assert 'href="/signup"' in r.text, "the 'New here?' line still has nowhere to go"
    assert "Create an account" in r.text

    # And the destination is real, not a 404 — the whole point of the copy change. `/signup`
    # returns a plain 200: it is a form to fill in, not a refusal.
    assert client.get("/signup").status_code == 200
