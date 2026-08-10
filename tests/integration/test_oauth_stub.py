"""Google OAuth stub — /auth/google/start and /auth/google/callback (A14, A15, §6 Step 8).

**"Stub" is about SCOPE, not rigor.** The flow verifies the ID token signature for
real; what makes it a stub is that it writes a waitlist row and stops. Per D8 and
§7-N2 it creates NO `users` row and NO session cookie — `SAAS_PRD:125` says there
is no `users` table before Phase 1, and building sessions now means building them
against a schema Phase 1 changes.

A15 is the security-critical one. An unverified ID token is a full authentication
bypass: anyone could mint `{"email": "someone@else.com"}` and land a row as them.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL unset — Phase 0 is Postgres-only (D3)",
)

CLIENT_ID = "test-client-id.apps.googleusercontent.com"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    env = {**os.environ, "MIGRATION_DATABASE_URL": TEST_DATABASE_URL}
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS waitlist CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-n", "landing", "upgrade", "head"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"landing migration failed:\n{result.stderr}"
    yield
    engine.dispose()


@pytest.fixture
def engine():
    eng = create_engine(TEST_DATABASE_URL, future=True)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM waitlist"))
    yield eng
    eng.dispose()


@pytest.fixture
def client(monkeypatch, engine):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SECRET_KEY", "test-signing-key")
    # http:// on purpose. The state cookie is set `secure=True` whenever the base
    # URL is https (correct in production), but TestClient speaks http://testserver,
    # so a secure cookie would never be sent back and every callback test would
    # fail on "missing state cookie" — a test artifact, not a bug.
    # test_state_cookie_is_secure_in_production covers the https case directly.
    monkeypatch.setenv("LANDING_BASE_URL", "http://testserver")

    from mihomes.landing import create_landing_app
    from mihomes.landing.db import reset_landing_engine

    reset_landing_engine()
    return TestClient(create_landing_app(), raise_server_exceptions=False)


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "oauth@example.com",
        "email_verified": True,
        "name": "OAuth Person",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


# --- /auth/google/start ---------------------------------------------------


def test_start_redirects_to_google(client):
    response = client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code in (302, 307)

    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert f"client_id={CLIENT_ID}" in location
    assert "response_type=code" in location
    assert "scope=openid+email+profile" in location or "scope=openid%20email%20profile" in location


def test_state_cookie_is_secure_and_httponly_in_production(monkeypatch):
    """Over https the state cookie must be Secure, HttpOnly and SameSite=Lax.

    SameSite must be Lax rather than Strict: the cookie has to survive Google's
    cross-site redirect back to the callback, and Strict would drop it, breaking
    every sign-in.
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SECRET_KEY", "test-signing-key")
    monkeypatch.setenv("LANDING_BASE_URL", "https://mihomes.ai")

    from mihomes.landing import create_landing_app

    response = TestClient(create_landing_app()).get(
        "/auth/google/start", follow_redirects=False
    )
    header = response.headers.get("set-cookie", "").lower()
    assert "secure" in header
    assert "httponly" in header
    assert "samesite=lax" in header


def test_start_uses_pkce_and_state(client):
    """§5.6 — PKCE plus state, both carried in a signed short-lived cookie.

    Without PKCE the authorization code is replayable; without state the callback
    is CSRF-able.
    """
    response = client.get("/auth/google/start", follow_redirects=False)
    location = response.headers["location"]

    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    assert "state=" in location
    assert any("oauth" in c.lower() for c in response.cookies.keys()), (
        "state/verifier must be stored in a cookie, not server memory"
    )


# --- /auth/google/callback: the happy path -------------------------------


def test_callback_creates_waitlist_row_only(client, engine, monkeypatch):
    """A14 — a valid ID token creates a waitlist row with source='google'.

    And nothing else: no users row, no session cookie (D8, §7-N2).
    """
    import mihomes.landing.oauth as oauth

    monkeypatch.setattr(oauth, "exchange_code", lambda **kw: {"id_token": "fake"})
    monkeypatch.setattr(oauth, "verify_id_token", lambda token, **kw: _claims())

    start = client.get("/auth/google/start", follow_redirects=False)
    state = oauth.read_state_cookie(start.cookies)

    response = client.get(
        f"/auth/google/callback?code=abc&state={state}", follow_redirects=False
    )
    assert response.status_code in (200, 302, 307)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT email, source, name FROM waitlist")
        ).one()
    assert row.email == "oauth@example.com"
    assert row.source == "google"
    assert row.name == "OAuth Person"

    # No session was established.
    for name in response.cookies.keys():
        assert "session" not in name.lower(), f"D8/N2: no session cookie, got {name!r}"


def test_callback_creates_no_users_table(client, engine, monkeypatch):
    """§7-N2 — there is no `users` table before Phase 1, and nothing here creates one."""
    import mihomes.landing.oauth as oauth

    monkeypatch.setattr(oauth, "exchange_code", lambda **kw: {"id_token": "fake"})
    monkeypatch.setattr(oauth, "verify_id_token", lambda token, **kw: _claims())

    start = client.get("/auth/google/start", follow_redirects=False)
    state = oauth.read_state_cookie(start.cookies)
    client.get(f"/auth/google/callback?code=abc&state={state}", follow_redirects=False)

    with engine.connect() as conn:
        tables = {
            r[0] for r in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
        }
    assert "users" not in tables
    assert "sessions" not in tables


# --- /auth/google/callback: the security cases ---------------------------


def test_callback_rejects_forged_token(client, engine, monkeypatch):
    """A15 — a bad signature is rejected.

    This is an authentication bypass if it ever regresses: an unverified token lets
    anyone mint {"email": "someone@else.com"} and take a row as them.
    """
    import mihomes.landing.oauth as oauth
    from mihomes.landing.oauth import OAuthError

    monkeypatch.setattr(oauth, "exchange_code", lambda **kw: {"id_token": "forged"})

    def reject(token, **kw):
        raise OAuthError("signature verification failed")

    monkeypatch.setattr(oauth, "verify_id_token", reject)

    start = client.get("/auth/google/start", follow_redirects=False)
    state = oauth.read_state_cookie(start.cookies)

    response = client.get(
        f"/auth/google/callback?code=abc&state={state}", follow_redirects=False
    )
    assert response.status_code in (400, 401, 403)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one()
    assert count == 0, "a forged token must not create a row"


def test_callback_rejects_mismatched_state(client, engine, monkeypatch):
    """State mismatch is CSRF — reject before touching the code."""
    import mihomes.landing.oauth as oauth

    called = []
    monkeypatch.setattr(
        oauth, "exchange_code", lambda **kw: called.append(1) or {"id_token": "x"}
    )

    client.get("/auth/google/start", follow_redirects=False)
    response = client.get(
        "/auth/google/callback?code=abc&state=not-the-state", follow_redirects=False
    )

    assert response.status_code in (400, 401, 403)
    assert not called, "the code must not be exchanged when state does not match"

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one() == 0


def test_callback_requires_a_verified_email(client, engine, monkeypatch):
    """An unverified Google email is not proof of address ownership.

    Accepting it would let someone add an address they do not control to the
    waitlist — and Phase 1 invites that cohort.
    """
    import mihomes.landing.oauth as oauth

    monkeypatch.setattr(oauth, "exchange_code", lambda **kw: {"id_token": "fake"})
    monkeypatch.setattr(
        oauth, "verify_id_token", lambda token, **kw: _claims(email_verified=False)
    )

    start = client.get("/auth/google/start", follow_redirects=False)
    state = oauth.read_state_cookie(start.cookies)
    response = client.get(
        f"/auth/google/callback?code=abc&state={state}", follow_redirects=False
    )
    assert response.status_code in (400, 401, 403)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one() == 0


def test_verify_id_token_checks_audience_and_issuer():
    """A signature alone is not enough — a token minted for another client is valid.

    Google signs tokens for every relying party with the same keys, so without an
    `aud` check a token issued to any other Google app would authenticate here.
    """
    from mihomes.landing.oauth import OAuthError, _validate_claims

    with pytest.raises(OAuthError, match="audience"):
        _validate_claims(_claims(aud="someone-elses-client-id"), client_id=CLIENT_ID)

    with pytest.raises(OAuthError, match="issuer"):
        _validate_claims(_claims(iss="https://evil.example.com"), client_id=CLIENT_ID)


def test_verify_id_token_rejects_an_expired_token():
    """Clearly expired, well outside the allowed clock skew."""
    from mihomes.landing.oauth import CLOCK_SKEW_SECONDS, OAuthError, _validate_claims

    stale = int(time.time()) - (CLOCK_SKEW_SECONDS + 3600)
    with pytest.raises(OAuthError, match="expired"):
        _validate_claims(_claims(exp=stale), client_id=CLIENT_ID)


def test_expiry_check_allows_a_token_inside_the_skew_window():
    """Small clock differences between us and Google must not reject a live token.

    The pair matters: without this, tightening the expiry comparison could silently
    become "reject everything slightly old", which breaks real sign-ins.
    """
    from mihomes.landing.oauth import _validate_claims

    fresh = int(time.time()) + 30
    assert _validate_claims(_claims(exp=fresh), client_id=CLIENT_ID)


def test_access_token_is_not_persisted(client, engine, monkeypatch):
    """§5.6 — discard the access token immediately (BILLING/ONBOARDING 3.2).

    Phase 0 needs the email and nothing else; a stored token is a liability with no
    corresponding capability.
    """
    import mihomes.landing.oauth as oauth

    monkeypatch.setattr(
        oauth, "exchange_code",
        lambda **kw: {"id_token": "fake", "access_token": "SECRET-ACCESS-TOKEN"},
    )
    monkeypatch.setattr(oauth, "verify_id_token", lambda token, **kw: _claims())

    start = client.get("/auth/google/start", follow_redirects=False)
    state = oauth.read_state_cookie(start.cookies)
    client.get(f"/auth/google/callback?code=abc&state={state}", follow_redirects=False)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM waitlist")).one()
    assert not any("SECRET-ACCESS-TOKEN" in str(v) for v in row), (
        "the access token must not be persisted anywhere on the row"
    )
