"""Settings → Sign-in methods (2026-10-19, SPEC-010 U2): add a password, change it, connect Google.

The refusals are the feature. Each one closes a way a borrowed session or a second Google account
could leave someone else holding a working credential, so each has its own test here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from mihomes.auth.csrf import CSRF_COOKIE
from mihomes.auth.oidc import IdentityClaims, claims_from_dict, upsert_user
from mihomes.auth.passwords import hash_password, verify_password
from mihomes.auth.sessions import SESSION_COOKIE

CSRF = "test-csrf-token"
GOOD_PW = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _console_mail(monkeypatch):
    # A provider that works without credentials, so the notice reaches the outbox.
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    from mihomes.auth.ratelimit import reset_all

    reset_all()


class _FakeGoogle:
    """Returns whatever claims the test sets; the OAuth plumbing itself is test_auth.py's."""

    def __init__(self):
        self.claims = {}

    def authorization_url(self, *, state, code_challenge):
        return f"https://accounts.google.invalid/auth?state={state}"

    def exchange_code(self, *, code, code_verifier):
        return "token"

    def verify(self, id_token):
        return claims_from_dict(self.claims)


@pytest.fixture
def google(monkeypatch):
    fake = _FakeGoogle()
    monkeypatch.setattr("mihomes.web.routes.auth._provider", lambda: fake)
    return fake


def _signed_in(web_client_as, role="owner", *, password: str | None = None, google=True):
    client = web_client_as(role)
    conn = web_client_as.connection
    client.cookies.set(CSRF_COOKIE, CSRF)
    conn.execute(
        text("update users set password_hash = :h, password_set_at = :t, "
             "google_sub = case when :g then google_sub else null end where id = :id"),
        {"h": hash_password(password) if password else None,
         "t": datetime.now(timezone.utc) if password else None,
         "g": google, "id": client.user_id},
    )
    return client, conn


def _user(conn, user_id):
    return conn.execute(
        text("select email, google_sub, password_hash from users where id = :i"), {"i": user_id}
    ).one()


def _age_session(conn, user_id, minutes=30):
    conn.execute(
        text("update sessions set created_at = :t where user_id = :u"),
        {"t": datetime.now(timezone.utc) - timedelta(minutes=minutes), "u": user_id},
    )


def _notices(conn, to):
    return conn.execute(
        text("select count(*) from email_outbox where template = 'sign_in_method_changed' "
             "and to_address = :t"), {"t": to},
    ).scalar()


def _google_round_trip(client, path):
    start = client.post(path, data={"csrf_token": CSRF}, follow_redirects=False)
    assert start.status_code == 303, start.text
    state = client.cookies.get("mihomes_oauth_state")
    return client.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)


# ── The page ─────────────────────────────────────────────────────────────────

def test_every_role_sees_the_section(web_client_as):
    for role, path in (("owner", "/settings"), ("staff", "/settings/connections")):
        client, _ = _signed_in(web_client_as, role)
        page = client.get(path)
        assert page.status_code == 200
        assert 'id="sign-in"' in page.text, f"{role} has no sign-in methods section"


def test_a_fresh_google_user_is_offered_the_password_form(web_client_as):
    client, _ = _signed_in(web_client_as)
    page = client.get("/settings").text
    assert 'action="/settings/sign-in/password"' in page
    assert "Connect Google" not in page


def test_a_stale_google_session_must_confirm_first(web_client_as):
    client, conn = _signed_in(web_client_as)
    _age_session(conn, client.user_id)
    page = client.get("/settings").text
    assert 'action="/settings/sign-in/google/confirm"' in page
    assert 'action="/settings/sign-in/password"' not in page


# ── Add a password ───────────────────────────────────────────────────────────

def test_google_user_adds_a_password(web_client_as):
    client, conn = _signed_in(web_client_as)
    r = client.post("/settings/sign-in/password",
                    data={"password": GOOD_PW, "confirm": GOOD_PW, "csrf_token": CSRF},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    assert "signin=password_added" in r.headers["location"]
    row = _user(conn, client.user_id)
    assert verify_password(GOOD_PW, row.password_hash)
    assert row.google_sub is not None, "adding a password must not drop Google"
    assert _notices(conn, row.email) == 1


def test_adding_a_password_needs_a_recent_sign_in(web_client_as):
    """A borrowed, long-lived session must not be able to add a permanent way in."""
    client, conn = _signed_in(web_client_as)
    _age_session(conn, client.user_id)
    r = client.post("/settings/sign-in/password",
                    data={"password": GOOD_PW, "confirm": GOOD_PW, "csrf_token": CSRF})
    assert r.status_code == 400
    assert "confirm it&#39;s you" in r.text
    assert _user(conn, client.user_id).password_hash is None


def test_adding_a_password_needs_csrf(web_client_as):
    client, conn = _signed_in(web_client_as)
    r = client.post("/settings/sign-in/password",
                    data={"password": GOOD_PW, "confirm": GOOD_PW, "csrf_token": "wrong"})
    assert r.status_code == 403
    assert _user(conn, client.user_id).password_hash is None


def test_mismatched_and_short_passwords_are_refused(web_client_as):
    client, conn = _signed_in(web_client_as)
    for pw, confirm, says in ((GOOD_PW, GOOD_PW + "x", "passwords don&#39;t match"), ("short", "short", "")):
        r = client.post("/settings/sign-in/password",
                        data={"password": pw, "confirm": confirm, "csrf_token": CSRF})
        assert r.status_code == 400
        assert says in r.text
    assert _user(conn, client.user_id).password_hash is None


def test_a_duplicate_password_row_on_the_address_is_refused_not_a_500(web_client_as):
    client, conn = _signed_in(web_client_as)
    email = _user(conn, client.user_id).email
    conn.execute(
        text("insert into users (id, email, password_hash, created_at) "
             "values (gen_random_uuid(), :e, 'scrypt$x', now())"), {"e": email.upper()},
    )
    r = client.post("/settings/sign-in/password",
                    data={"password": GOOD_PW, "confirm": GOOD_PW, "csrf_token": CSRF})
    assert r.status_code == 400
    assert "Another MiHomes account" in r.text


# ── Confirm with Google (re-auth) ────────────────────────────────────────────

def test_confirming_with_the_same_google_account_unlocks_the_form(web_client_as, google):
    client, conn = _signed_in(web_client_as)
    _age_session(conn, client.user_id)
    row = _user(conn, client.user_id)
    google.claims = {"sub": row.google_sub, "email": row.email, "email_verified": True}

    r = _google_round_trip(client, "/settings/sign-in/google/confirm")
    assert r.status_code == 303, r.text
    assert "signin=confirmed" in r.headers["location"]
    page = client.get("/settings").text
    assert 'action="/settings/sign-in/password"' in page, "re-auth did not refresh the session"


def test_confirming_with_a_different_google_account_is_refused(web_client_as, google):
    """`prompt=select_account` lets them pick anyone; only their own Google account counts."""
    client, conn = _signed_in(web_client_as)
    _age_session(conn, client.user_id)
    google.claims = {"sub": "someone-else", "email": "x@example.com", "email_verified": True}
    r = _google_round_trip(client, "/settings/sign-in/google/confirm")
    assert "signin_error=reauth_mismatch" in r.headers["location"]
    assert 'action="/settings/sign-in/password"' not in client.get("/settings").text


# ── Connect Google ───────────────────────────────────────────────────────────

def test_password_user_connects_google(web_client_as, google):
    client, conn = _signed_in(web_client_as, password=GOOD_PW, google=False)
    email = _user(conn, client.user_id).email
    session_before = client.cookies.get(SESSION_COOKIE)
    google.claims = {"sub": "new-google-sub", "email": email.upper(), "email_verified": True}

    r = _google_round_trip(client, "/settings/sign-in/google/connect")
    assert r.status_code == 303, r.text
    assert "signin=google_connected" in r.headers["location"]
    assert _user(conn, client.user_id).google_sub == "new-google-sub"
    assert client.cookies.get(SESSION_COOKIE) == session_before, "link mode must not sign in anew"
    assert not client.cookies.get("mihomes_oauth_intent")
    assert _notices(conn, email) == 1
    assert "Google sign-in is connected" in client.get(r.headers["location"]).text


def test_a_google_account_with_another_email_is_refused(web_client_as, google):
    """The email match is the security control: it proves mailbox ownership, and stops a stolen
    session from linking the thief's own Google account as a back door."""
    client, conn = _signed_in(web_client_as, password=GOOD_PW, google=False)
    google.claims = {"sub": "attacker-sub", "email": "attacker@example.com",
                     "email_verified": True}
    r = _google_round_trip(client, "/settings/sign-in/google/connect")
    assert "signin_error=google_mismatch" in r.headers["location"]
    assert _user(conn, client.user_id).google_sub is None


def test_a_google_account_already_used_elsewhere_is_refused(web_client_as, google):
    client, conn = _signed_in(web_client_as, password=GOOD_PW, google=False)
    other, _ = _signed_in(web_client_as, "admin")
    other_sub = _user(conn, other.user_id).google_sub
    google.claims = {"sub": other_sub, "email": _user(conn, client.user_id).email,
                     "email_verified": True}
    r = _google_round_trip(client, "/settings/sign-in/google/connect")
    assert "signin_error=google_taken" in r.headers["location"]
    assert _user(conn, client.user_id).google_sub is None


def test_connecting_needs_csrf(web_client_as, google):
    client, _ = _signed_in(web_client_as, password=GOOD_PW, google=False)
    r = client.post("/settings/sign-in/google/connect", data={"csrf_token": "nope"},
                    follow_redirects=False)
    assert r.status_code == 403


def test_a_leftover_intent_does_not_hijack_a_plain_sign_in(web_client_as, google):
    """An abandoned "Connect Google" must not turn the next ordinary sign-in into a link."""
    client, _ = _signed_in(web_client_as, password=GOOD_PW, google=False)
    client.post("/settings/sign-in/google/connect", data={"csrf_token": CSRF},
                follow_redirects=False)
    assert client.cookies.get("mihomes_oauth_intent")
    client.get("/auth/google/start", follow_redirects=False)
    assert not client.cookies.get("mihomes_oauth_intent")


def test_the_flow_finishes_only_for_the_user_who_started_it(web_client_as, google):
    client, conn = _signed_in(web_client_as, password=GOOD_PW, google=False)
    client.post("/settings/sign-in/google/connect", data={"csrf_token": CSRF},
                follow_redirects=False)
    other, _ = _signed_in(web_client_as, "admin")
    client.cookies.set(SESSION_COOKIE, other.cookies.get(SESSION_COOKIE))
    google.claims = {"sub": "s", "email": _user(conn, client.user_id).email,
                     "email_verified": True}
    state = client.cookies.get("mihomes_oauth_state")
    r = client.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert _user(conn, client.user_id).google_sub is None


# ── Change password ──────────────────────────────────────────────────────────

def test_changing_the_password_signs_out_other_devices(web_client_as):
    client, conn = _signed_in(web_client_as, password=GOOD_PW)
    old_session = client.cookies.get(SESSION_COOKIE)
    new_pw = "a different long passphrase"
    r = client.post("/settings/sign-in/password/change",
                    data={"current_password": GOOD_PW, "password": new_pw, "confirm": new_pw,
                          "csrf_token": CSRF}, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert "signin=password_changed" in r.headers["location"]
    assert verify_password(new_pw, _user(conn, client.user_id).password_hash)
    new_session = r.cookies.get(SESSION_COOKIE)
    assert new_session and new_session != old_session
    # The fixture set its cookie without a domain; replace it as a browser would.
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, new_session)
    sessions = conn.execute(text("select count(*) from sessions where user_id = :u"),
                            {"u": client.user_id}).scalar()
    assert sessions == 1, "other sessions survived a password change"
    assert client.get("/settings").status_code == 200, "the new session does not work"


def test_changing_needs_the_current_password(web_client_as):
    client, conn = _signed_in(web_client_as, password=GOOD_PW)
    new_pw = "a different long passphrase"
    r = client.post("/settings/sign-in/password/change",
                    data={"current_password": "wrong", "password": new_pw, "confirm": new_pw,
                          "csrf_token": CSRF})
    assert r.status_code == 400
    assert "current password is incorrect" in r.text
    assert verify_password(GOOD_PW, _user(conn, client.user_id).password_hash)


def test_current_password_guesses_are_throttled(web_client_as):
    from mihomes.auth.ratelimit import EMAIL_MAX_FAILURES

    client, _ = _signed_in(web_client_as, password=GOOD_PW)
    data = {"current_password": "wrong", "password": "x" * 20, "confirm": "x" * 20,
            "csrf_token": CSRF}
    for _ in range(EMAIL_MAX_FAILURES):
        client.post("/settings/sign-in/password/change", data=data)
    r = client.post("/settings/sign-in/password/change",
                    data={**data, "current_password": GOOD_PW})
    assert r.status_code == 400
    assert "Too many attempts" in r.text


# ── Google sign-in must not move a password user's login email ───────────────

def test_google_sign_in_leaves_a_password_users_email_alone(db_session_for_users):
    db = db_session_for_users
    user = upsert_user(db, IdentityClaims(subject="dual", email="me@example.com"))
    user.password_hash = "scrypt$x"
    db.flush()
    again = upsert_user(db, IdentityClaims(subject="dual", email="renamed@example.com"))
    assert again.email == "me@example.com"


@pytest.fixture
def db_session_for_users(_pg_engine):
    from sqlalchemy.orm import sessionmaker

    connection = _pg_engine.connect()
    transaction = connection.begin()
    s = sessionmaker(bind=connection, future=True, join_transaction_mode="create_savepoint")()
    try:
        yield s
    finally:
        s.close()
        transaction.rollback()
        connection.close()
