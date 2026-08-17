"""G12 · §6 Step 12 — Google sign-in, sessions, cookies, revocation (A15, A16, A17).

The identity provider is **faked at the Protocol boundary**, not by patching HTTP. A test that
monkeypatched `urlopen` would be asserting against my mock of Google's wire format; substituting an
`IdentityProvider` asserts against the contract the application actually depends on. The *real*
verifier is SPEC-001's `landing/oauth.py` and has its own tests — reused here rather than
reimplemented, so `test_rejects_forged_token` exercises the claim-validation half that G12 adds.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from mihomes.auth import sessions as sessions_mod
from mihomes.auth.csrf import CSRF_COOKIE
from mihomes.auth.oidc import IdentityClaims, InvalidIdentityToken, claims_from_dict, upsert_user
from mihomes.auth.sessions import (
    SESSION_COOKIE,
    create_session,
    hash_session_id,
    lookup_session,
    revoke_all_sessions,
    set_current_account,
)
from mihomes.models.membership import Membership
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User
from mihomes.web.app import create_app
from mihomes.web.deps import get_db

# --------------------------------------------------------------------------------------
# A fake provider at the Protocol boundary
# --------------------------------------------------------------------------------------

class _FakeProvider:
    """Deterministic stand-in. `verify` accepts exactly one token; anything else is 'forged'."""

    def __init__(self, *, good_token: str = "good-token", claims: dict | None = None):
        self.good_token = good_token
        self.claims = claims or {
            "sub": "google-sub-12345",
            "email": "owner@example.com",
            "email_verified": True,
            "name": "Owner Person",
        }
        self.exchanged: list[tuple[str, str]] = []

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://accounts.google.invalid/auth?state={state}&cc={code_challenge}"

    def exchange_code(self, *, code: str, code_verifier: str) -> str:
        self.exchanged.append((code, code_verifier))
        if code == "bad-code":
            raise InvalidIdentityToken("code exchange failed")
        return self.good_token if code == "good-code" else "forged-token"

    def verify(self, id_token: str) -> IdentityClaims:
        if id_token != self.good_token:
            raise InvalidIdentityToken("signature verification failed")
        return claims_from_dict(self.claims)


@pytest.fixture
def provider(monkeypatch):
    fake = _FakeProvider()
    monkeypatch.setattr("mihomes.web.routes.auth._provider", lambda: fake)
    return fake


@pytest.fixture
def client(_pg_engine, provider):
    """A TestClient on the shared Postgres schema, with `get_db` overridden.

    `base_url` is `http://testserver.local` — deliberately **not** localhost — so the `Secure`
    cookie flag is exercised. On loopback the app drops `Secure` (a browser will not store a Secure
    cookie over plain http), so a localhost client could never observe A16's requirement.
    """
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
    # The Host guard accepts loopback by default; this host has to be allowed explicitly, and
    # `raise_server_exceptions=False` so a 4xx is observable rather than re-raised.
    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as c:
        c._Session = Session
        yield c

    transaction.rollback()
    connection.close()


@pytest.fixture
def db(_pg_engine):
    """A plain session for the unit-level session-store tests."""
    connection = _pg_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection, future=True, join_transaction_mode="create_savepoint")
    s = Session()
    try:
        yield s
    finally:
        s.close()
        transaction.rollback()
        connection.close()


def _grant(db, account_id, user_id, *, role: str = "owner", status: str = "active") -> None:
    """Insert a membership with a Core insert.

    Not `db.add(Membership(...))`: `Membership` is `TenantOwned`, so the ORM path invokes the G8
    stamp listener and demands an account context — the very thing authentication runs before. The
    production code reads memberships the same way and for the same reason (see
    `auth/sessions.py`'s module docstring), so the fixture matches the code under test rather than
    working around it.
    """
    db.execute(
        Membership.__table__.insert().values(
            id=uuid.uuid4(),
            account_id=account_id,
            user_id=user_id,
            role=role,
            status=status,
        )
    )
    db.flush()


def _make_user(db, sub: str = "sub-1") -> User:
    user = User(id=uuid.uuid4(), google_sub=sub, email=f"{sub}@example.com")
    db.add(user)
    db.flush()
    return user


def _make_account(db, slug: str) -> uuid.UUID:
    account_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO accounts (id, slug, name, type, plan) "
            "VALUES (:i, :s, :s, 'household', 'free')"
        ),
        {"i": account_id, "s": slug},
    )
    return account_id


# --------------------------------------------------------------------------------------
# A15 — the sign-in flow, and forged tokens
# --------------------------------------------------------------------------------------

def test_signin_flow(client, provider):
    """A15 — a full round trip creates a `users` row and a session.

    Follows the real sequence: `/start` sets state + verifier cookies, `/callback` consumes them.
    Driving the callback without the start step would test a shortcut nobody takes.
    """
    start = client.get("/auth/google/start", follow_redirects=False)
    assert start.status_code == 307
    assert "accounts.google.invalid" in start.headers["location"]

    state = client.cookies.get("mihomes_oauth_state")
    assert state, "the anti-forgery state cookie was not set"

    resp = client.get(
        f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False
    )
    assert resp.status_code == 303, resp.text

    with client._Session() as s:
        user = s.execute(
            select(User).where(User.google_sub == "google-sub-12345")
        ).scalar_one()
        assert user.email == "owner@example.com"
        row = s.execute(
            select(SessionRow).where(SessionRow.user_id == user.id)
        ).scalar_one()
        # The raw id must NOT be in the database.
        raw = client.cookies.get(SESSION_COOKIE)
        assert raw, "no session cookie was issued"
        assert row.session_id_hash == hash_session_id(raw)
        assert raw not in (row.session_id_hash,), "the raw session id was stored"

    # The flow cookies are cleared once used, so a captured verifier is not reusable.
    assert not client.cookies.get("mihomes_oauth_verifier")


def test_rejects_forged_token(client, provider):
    """A15 — a token that does not verify must not create a user or a session."""
    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")

    resp = client.get(
        f"/auth/google/callback?code=whatever&state={state}", follow_redirects=False
    )

    assert resp.status_code == 401, resp.text
    assert not client.cookies.get(SESSION_COOKIE), "a session was issued for a forged token"
    with client._Session() as s:
        assert s.execute(select(SessionRow)).first() is None


def test_callback_rejects_a_mismatched_state(client, provider):
    """The anti-forgery check on the OAuth flow itself.

    Without it, an attacker can start their own flow and feed the victim the resulting callback URL,
    landing the victim in the attacker's session (login CSRF).
    """
    client.get("/auth/google/start", follow_redirects=False)
    resp = client.get(
        "/auth/google/callback?code=good-code&state=attacker-chosen", follow_redirects=False
    )
    assert resp.status_code == 400
    assert not client.cookies.get(SESSION_COOKIE)


def test_callback_without_starting_is_rejected(client, provider):
    """No state cookie at all must not be treated as a match.

    `tokens_match` refuses empty values for exactly this case — a naive `expected != given` compares
    two absent values as equal.
    """
    resp = client.get("/auth/google/callback?code=good-code&state=", follow_redirects=False)
    assert resp.status_code == 400


def test_unverified_email_is_refused(client, monkeypatch):
    """Google will issue a token for an unverified address; that is not an identity.

    Accepting it would let someone sign in as an address they do not control, if the provider ever
    permits an unverified sign-up.
    """
    fake = _FakeProvider(claims={
        "sub": "sub-unverified", "email": "nope@example.com", "email_verified": False,
    })
    monkeypatch.setattr("mihomes.web.routes.auth._provider", lambda: fake)

    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")
    resp = client.get(
        f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False
    )
    assert resp.status_code == 401
    assert not client.cookies.get(SESSION_COOKIE)


def test_missing_email_verified_claim_is_not_treated_as_verified():
    """An absent claim must not read as True — the common shape of this bug."""
    with pytest.raises(InvalidIdentityToken):
        claims_from_dict({"sub": "s", "email": "a@b.c"})


def test_identity_is_keyed_on_sub_not_email(db):
    """`sub` is stable forever; an email can change hands.

    Upserting on email would eventually hand one person's estate to whoever inherited their address.
    This asserts a changed email updates the same user rather than creating a second one.
    """
    first = upsert_user(db, IdentityClaims(subject="stable-sub", email="old@example.com"))
    second = upsert_user(db, IdentityClaims(subject="stable-sub", email="new@example.com"))
    assert first.id == second.id, "a changed email created a second user"
    assert second.email == "new@example.com", "the display email was not refreshed"

    # And the same email under a different sub is a *different* person.
    other = upsert_user(db, IdentityClaims(subject="other-sub", email="new@example.com"))
    assert other.id != first.id


# --------------------------------------------------------------------------------------
# A16 — cookie flags
# --------------------------------------------------------------------------------------

def test_cookie_flags(client, provider):
    """A16 — the session cookie is httpOnly + Secure + SameSite=Lax.

    Read from the raw `set-cookie` header rather than the cookie jar: the jar exposes some
    attributes and normalises others, so asserting on it can pass while the header is wrong.
    """
    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")
    resp = client.get(
        f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False
    )

    session_cookies = [
        h for h in resp.headers.get_list("set-cookie") if h.startswith(f"{SESSION_COOKIE}=")
    ]
    assert session_cookies, "no session cookie header was sent"
    header = session_cookies[0].lower()

    assert "httponly" in header, "the session cookie is readable by script"
    assert "samesite=lax" in header, "the session cookie is sent on cross-site requests"
    # base_url is http://localhost, and the app drops Secure on loopback so a browser will store it.
    assert "secure" not in header, (
        "Secure was set on a loopback request; a browser refuses such a cookie over http and "
        "sign-in would be impossible in development"
    )


def test_secure_flag_is_set_on_a_non_loopback_host():
    """The other half of A16: off loopback, `Secure` must be present.

    Asserted against `_set_cookie` directly rather than through a `TestClient`. The first version
    drove a request at `https://app.example.com`, which `HostAndOriginGuardMiddleware` rejects with a
    400 — so it ended in `pytest.skip`, and a **conditionally skipped security assertion is a red
    gate** (conventions §0). Worse, it would have skipped silently forever while nothing verified the
    production cookie was sent over TLS only.

    Calling the helper with a stand-in request is deterministic and covers the real decision: a test
    that only ever used localhost would report A16 green while the deployed cookie travelled in
    clear text.
    """
    from types import SimpleNamespace

    from fastapi.responses import Response

    from mihomes.web.routes.auth import _set_cookie

    for host, expect_secure in (
        ("app.example.com", True),
        ("mihomes.fly.dev", True),
        ("localhost", False),
        ("127.0.0.1", False),
    ):
        response = Response()
        request = SimpleNamespace(url=SimpleNamespace(hostname=host))
        _set_cookie(response, request, SESSION_COOKIE, "value", max_age=60)
        header = response.headers.get("set-cookie", "").lower()

        assert "httponly" in header and "samesite=lax" in header, header
        if expect_secure:
            assert "secure" in header, (
                f"the session cookie is not Secure on {host!r} — it would be sent over plain HTTP"
            )
        else:
            assert "secure" not in header, (
                f"Secure was set on loopback host {host!r}; a browser refuses such a cookie over "
                "http and sign-in would be impossible in development"
            )


def test_is_loopback_decides_the_secure_flag():
    """The decision is made from the request host, not a DEBUG flag.

    A flag can be set wrong in production; "the request arrived at localhost" cannot. Asserted
    directly because the route-level test above depends on the Host guard's mood.
    """
    from types import SimpleNamespace

    from mihomes.web.routes.auth import _is_loopback

    for host in ("localhost", "127.0.0.1", "::1"):
        assert _is_loopback(SimpleNamespace(url=SimpleNamespace(hostname=host))) is True
    for host in ("app.example.com", "mihomes.fly.dev", "192.168.1.10"):
        assert _is_loopback(SimpleNamespace(url=SimpleNamespace(hostname=host))) is False


# --------------------------------------------------------------------------------------
# A17 — revocation takes effect on the next request
# --------------------------------------------------------------------------------------

def test_revocation_immediate(db):
    """A17 — revoking a membership denies access on the **next request**.

    The session row is untouched and unexpired; only the membership status changed. This is why
    `lookup_session` re-reads the membership every time instead of deciding once at sign-in: a
    cached decision would leave a removed user with access until their session expired, which for a
    14-day TTL is not "immediate" by any reading.
    """
    user = _make_user(db, "sub-revoke")
    account_id = _make_account(db, f"revoke-{uuid.uuid4().hex[:8]}")
    _grant(db, account_id, user.id, role="owner")

    raw, row = create_session(db, user.id)
    assert set_current_account(db, row.id, account_id) is True

    # Before revocation: authorised, with the role resolved.
    before = lookup_session(db, raw)
    assert before is not None and before.account_id == account_id and before.role == "owner"

    db.execute(
        Membership.__table__.update()
        .where(Membership.__table__.c.user_id == user.id)
        .values(status="revoked")
    )
    db.flush()

    # The very next lookup denies it. Same session, same cookie, nothing expired.
    assert lookup_session(db, raw) is None, (
        "a revoked membership still authorised the session — A17 requires the next request to be "
        "denied, not the next expiry"
    )


def test_revoked_membership_denies_rather_than_downgrading(db):
    """A revoked user must not fall back to "signed in, no account".

    That would be worse than it sounds: the account picker would let them re-select the very account
    they were removed from, and `set_current_account` is the only thing standing in the way.
    """
    user = _make_user(db, "sub-downgrade")
    account_id = _make_account(db, f"down-{uuid.uuid4().hex[:8]}")
    _grant(db, account_id, user.id, role="staff", status="revoked")
    raw, row = create_session(db, user.id)
    row.current_account_id = account_id
    db.flush()

    assert lookup_session(db, raw) is None
    # And they cannot re-bind it either.
    assert set_current_account(db, row.id, account_id) is False


def test_cannot_bind_an_account_without_a_membership(db):
    """The account arrives from a form the user controls, so it is verified, not trusted.

    Without this check a user could bind any account id and every later request would accept it —
    `lookup_session` would find an active membership check to run only because the session claimed
    the account.
    """
    user = _make_user(db, "sub-nomember")
    someone_elses = _make_account(db, f"theirs-{uuid.uuid4().hex[:8]}")
    _raw, row = create_session(db, user.id)

    assert set_current_account(db, row.id, someone_elses) is False
    db.refresh(row)
    assert row.current_account_id is None


# --------------------------------------------------------------------------------------
# The session store's own properties
# --------------------------------------------------------------------------------------

def test_only_the_hash_is_stored(db):
    """A database disclosure must not yield usable sessions."""
    user = _make_user(db, "sub-hash")
    raw, row = create_session(db, user.id)

    assert row.session_id_hash != raw
    assert len(row.session_id_hash) == 64, "expected a sha256 hex digest"
    # The raw value appears nowhere in the row.
    assert raw not in str(row.__dict__)


def test_expired_session_is_not_authorised(db):
    user = _make_user(db, "sub-expired")
    raw, row = create_session(db, user.id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()
    assert lookup_session(db, raw) is None


def test_unknown_and_missing_cookies_are_not_authorised(db):
    assert lookup_session(db, None) is None
    assert lookup_session(db, "") is None
    assert lookup_session(db, "not-a-real-session-id") is None


def test_signout_everywhere_ends_every_session(db):
    """The count is asserted, so this cannot pass by ending only the current session."""
    user = _make_user(db, "sub-everywhere")
    raws = [create_session(db, user.id)[0] for _ in range(3)]
    other = _make_user(db, "sub-bystander")
    other_raw, _ = create_session(db, other.id)

    ended = revoke_all_sessions(db, user.id)
    db.flush()

    assert ended == 3
    for raw in raws:
        assert lookup_session(db, raw) is None
    # Another user's session is untouched.
    assert lookup_session(db, other_raw) is not None


def test_session_id_is_high_entropy(db):
    """A guessable session id is the whole ballgame, so the width is asserted rather than assumed."""
    user = _make_user(db, "sub-entropy")
    raws = {create_session(db, user.id)[0] for _ in range(200)}
    assert len(raws) == 200, "session ids collided"
    # 32 bytes of token_urlsafe is 43 characters.
    assert all(len(r) >= 40 for r in raws), "session ids are shorter than expected"


# --------------------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------------------

def test_signout_requires_a_matching_csrf_token(client, provider):
    """A third-party page can cause a request with the victim's cookies but cannot read them."""
    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")
    client.get(f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False)
    assert client.cookies.get(SESSION_COOKIE)

    bad = client.post("/signout", data={"csrf_token": "wrong"}, follow_redirects=False)
    assert bad.status_code == 403
    assert client.cookies.get(SESSION_COOKIE), "the session was ended despite a bad CSRF token"

    good = client.post(
        "/signout",
        data={"csrf_token": client.cookies.get(CSRF_COOKIE)},
        follow_redirects=False,
    )
    assert good.status_code == 303


def test_csrf_comparison_is_constant_time_and_rejects_blanks():
    """`hmac.compare_digest`, not `==`.

    A short-circuiting comparison leaks how many leading characters matched. Also: two absent values
    must not compare equal, which a naive check would allow.
    """
    from mihomes.auth.csrf import tokens_match

    assert tokens_match("abc", "abc") is True
    assert tokens_match("abc", "abd") is False
    assert tokens_match(None, None) is False
    assert tokens_match("", "") is False
    assert tokens_match("abc", None) is False


def test_csrf_cookie_is_readable_by_script_but_the_session_is_not(client, provider):
    """The asymmetry is deliberate and worth pinning.

    The CSRF cookie must be script-readable (the page echoes it into a form field) and carries no
    authority on its own. The session cookie must never be script-readable, because it carries all
    of it.
    """
    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")
    resp = client.get(
        f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False
    )
    headers = {h.split("=")[0]: h.lower() for h in resp.headers.get_list("set-cookie")}
    assert "httponly" in headers[SESSION_COOKIE]
    assert "httponly" not in headers[CSRF_COOKIE]


# --------------------------------------------------------------------------------------
# Session fixation
# --------------------------------------------------------------------------------------

def test_signin_rotates_the_session_id(client, provider):
    """A planted session id must not survive sign-in.

    Fixation: an attacker sets a session cookie they know in the victim's browser, waits for them to
    sign in, then holds an authenticated session. Rotating on sign-in means the id the attacker knows
    is never the one that ends up authenticated.

    **This test had no teeth until mutation testing caught it**, and the reasons are worth keeping —
    both are easy to repeat:

    1. It planted a session for a *different* user (`sub-fixation`) than sign-in resolves to
       (`google-sub-12345`). `create_session` mints a fresh id regardless, so `issued != planted`
       passed whether or not rotation happened.
    2. It planted the row on the `db` fixture's connection, which the app never sees — so the
       "planted session is gone" assertion was checking a row that was never visible.

    Now the session is planted for the **same** user, through the **app's own** session factory, so
    removing the rotation genuinely resurfaces here.
    """
    # Sign in once to establish the user sign-in will resolve to.
    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")
    client.get(f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False)

    with client._Session() as s:
        user = s.execute(
            select(User).where(User.google_sub == "google-sub-12345")
        ).scalar_one()
        # Plant a second session for that same user, on the connection the app uses.
        planted, _row = create_session(s, user.id)
        s.commit()

    # Present the planted cookie and sign in again.
    client.cookies.set(SESSION_COOKIE, planted)
    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")
    resp = client.get(
        f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False
    )

    issued_headers = [
        h for h in resp.headers.get_list("set-cookie") if h.startswith(f"{SESSION_COOKIE}=")
    ]
    assert issued_headers, "sign-in issued no session cookie"
    issued = issued_headers[0].split("=", 1)[1].split(";")[0]
    assert issued != planted, "the pre-existing session id was reused after sign-in"

    # And the planted session is *gone*, not merely superseded — otherwise the attacker's id would
    # keep authenticating until it expired. This is the assertion the mutation must break.
    with client._Session() as s:
        assert s.execute(
            select(SessionRow).where(SessionRow.session_id_hash == hash_session_id(planted))
        ).first() is None, (
            "the planted session survived sign-in — an attacker who set that cookie still holds an "
            "authenticated session"
        )


def test_session_ttl_is_bounded():
    """A session that never expires is a credential with no revocation path."""
    assert timedelta(days=1) <= sessions_mod.SESSION_TTL <= timedelta(days=30)
