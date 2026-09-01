"""G5 · SPEC-010 §6 Step 5 — password reset (A11, A12, A13).

**A13 is the criterion the spec flags as most likely to be forgotten, and this file asserts it
first for that reason.** The failure is invisible from the outside: set a new password, sign in,
everything works. What silently does not happen is the *other* sessions dying — and the person
resetting under duress is doing it precisely to evict whoever else is signed in. A reset that
leaves them there has changed the lock while the intruder is still inside.

So A13 counts surviving rows and requires **zero** (harness §2). "The old cookie stopped working"
is not the assertion: it passes when one session of three survived.

A11's oracle is A7's in mirror image. A7 compared two *failures*; A11 compares two *successes* —
requesting a reset for an address that does not exist must look exactly like requesting one that
does. That makes the positive twin non-optional (§0.5b): mail must actually be sent for the real
address, or a route that does nothing at all satisfies A11 perfectly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from mihomes.auth.password_identity import create_password_user
from mihomes.auth.password_reset import (
    hash_token,
    issue_reset_token,
    verify_reset_token,
)
from mihomes.auth.ratelimit import reset_all as reset_ratelimit
from mihomes.auth.sessions import SESSION_COOKIE, create_session
from mihomes.models.password_reset_token import PasswordResetToken
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User
from mihomes.web.app import create_app
from mihomes.web.deps import get_db

OLD_PASSWORD = "the-original-passphrase"
NEW_PASSWORD = "a-brand-new-passphrase"


@pytest.fixture(autouse=True)
def _clean_ratelimit():
    """The reset route is throttled too (§6 Step 4 — "login and reset")."""
    reset_ratelimit()
    yield
    reset_ratelimit()


@pytest.fixture(autouse=True)
def _console_email(monkeypatch):
    """P4 — the `console` provider, never a mocked `resend`.

    The factory defaults to Resend and raises without `RESEND_API_KEY`, so this is required
    rather than tidy. Mocking `resend` instead would assert against my model of their client;
    `console` is a real `EmailProvider` and exercises the same `_send` path, including the
    suppression check D8 turns on.
    """
    monkeypatch.setenv("EMAIL_PROVIDER", "console")


@pytest.fixture
def client(_pg_engine):
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


def _make_user(client, email: str, password: str = OLD_PASSWORD) -> uuid.UUID:
    s = client._Session()
    try:
        user = create_password_user(s, email=email, password=password)
        s.commit()
        return user.id
    finally:
        s.close()


def _session_count(client, user_id) -> int:
    s = client._Session()
    try:
        return s.execute(
            select(func.count()).select_from(SessionRow).where(SessionRow.user_id == user_id)
        ).scalar_one()
    finally:
        s.close()


@pytest.fixture
def sent(monkeypatch):
    """Every message that reaches the provider, captured at the `EmailProvider` boundary.

    **Not the outbox, and the reason is a real architectural fact rather than a testing
    convenience.** `_send` enqueues only when an account is bound; with a session but no account
    it calls `_send_inline` (`service.py:105-111`). **A password reset happens before sign-in,
    so no account is ever bound** — the same carve-out SPEC-001's waitlist confirmation holds,
    and for the same reason: an outbox row with no owner could never be drained, because RLS
    would never select it.

    So there is no outbox row to count, and asserting on one would have failed against correct
    code. Captured at the provider instead, which is where both paths converge.
    """
    seen: list[dict] = []
    from mihomes.services.email import console_provider

    real = console_provider.ConsoleProvider.send

    def capture(self, to, subject, html, **kw):
        seen.append({"to": to, "subject": subject, "html": html, "text": kw.get("text")})
        return real(self, to, subject, html, **kw)

    monkeypatch.setattr(console_provider.ConsoleProvider, "send", capture)
    return seen


def _sent_to(sent, to: str) -> int:
    return sum(1 for m in sent if m["to"] == to)


def _mint_token(client, user_id) -> str:
    """A live token, through the app's own session factory.

    On the app's connection, not a separate one — `test_auth.py:590` records what happens
    otherwise: the row is invisible to the route and the assertion checks something that was
    never there.
    """
    s = client._Session()
    try:
        raw = issue_reset_token(s, user_id)
        s.commit()
        return raw
    finally:
        s.close()


# ── A13 — every session dies. Built first: it is the invisible one. ───────────

def test_reset_revokes_all_sessions(client):
    """**A13 · G-revoke** — after a reset, the surviving session count is **zero**.

    Three sessions, not one. `revoke_all_sessions` with a broken filter — or a route that only
    revokes the current cookie — passes a one-session test and leaves every other device signed
    in, which is the exact failure this criterion exists to prevent.
    """
    user_id = _make_user(client, "duress@example.com")

    s = client._Session()
    try:
        for _ in range(3):
            create_session(s, user_id)
        s.commit()
    finally:
        s.close()

    assert _session_count(client, user_id) == 3, "the fixture did not create three sessions"

    # A bystander, to catch a revocation that logs out the whole table.
    other_id = _make_user(client, "bystander@example.com")
    s = client._Session()
    try:
        create_session(s, other_id)
        s.commit()
    finally:
        s.close()

    raw = _mint_token(client, user_id)
    r = client.post(
        f"/password/reset/{raw}", data={"password": NEW_PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 303, r.text

    # **The assertion.** Not "the old cookie 404s" — a count, and it must be the one session the
    # reset itself issued.
    surviving = _session_count(client, user_id)
    assert surviving == 1, (
        f"{surviving} sessions survive for this user; expected exactly the one the reset "
        "issued. Every other device is still signed in, so the person who reset their password "
        "to evict an intruder has not evicted them (A13)"
    )

    # The positive twins, both required. The new password works...
    client.cookies.clear()
    ok = client.post(
        "/login",
        data={"email": "duress@example.com", "password": NEW_PASSWORD},
        follow_redirects=False,
    )
    assert ok.status_code == 303, "the new password does not sign in"

    # ...the old one does not...
    client.cookies.clear()
    stale = client.post(
        "/login",
        data={"email": "duress@example.com", "password": OLD_PASSWORD},
        follow_redirects=False,
    )
    assert stale.status_code == 401, "the OLD password still works after a reset"

    # ...and a bystander was not logged out by a filter that matched everyone.
    assert _session_count(client, other_id) == 1, (
        "another user's session was revoked — the WHERE clause is not scoped to this user"
    )


# ── A12 — single-use and expiry ───────────────────────────────────────────────

def test_token_single_use_and_expiry(client):
    """**A12** — a token works once, and not after it expires.

    Both halves need the positive twin that a *valid, unused* token works, or a `verify` that
    returns None unconditionally satisfies every negative assertion here.
    """
    user_id = _make_user(client, "once@example.com")
    raw = _mint_token(client, user_id)

    # The twin, first: it works.
    s = client._Session()
    try:
        assert verify_reset_token(s, raw) is not None, "a fresh token does not verify"
    finally:
        s.close()

    r = client.post(
        f"/password/reset/{raw}", data={"password": NEW_PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 303, r.text

    # --- single use ---------------------------------------------------------
    second = "a-third-distinct-passphrase"
    again = client.post(
        f"/password/reset/{raw}", data={"password": second}, follow_redirects=False
    )
    assert again.status_code == 400, "the same reset link worked twice"

    # And the second password was not applied — a refusal that still writes is not a refusal.
    client.cookies.clear()
    assert client.post(
        "/login", data={"email": "once@example.com", "password": second},
        follow_redirects=False,
    ).status_code == 401, "the reused link changed the password anyway"

    client.cookies.clear()
    assert client.post(
        "/login", data={"email": "once@example.com", "password": NEW_PASSWORD},
        follow_redirects=False,
    ).status_code == 303, "the first reset's password stopped working"

    # `used_at` is stamped rather than the row deleted, so a used link can be told apart from
    # one that never existed when someone reports a link that "didn't work".
    s = client._Session()
    try:
        row = s.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == hash_token(raw)
            )
        ).scalar_one()
        assert row.used_at is not None, "the token row was not marked used"
    finally:
        s.close()

    # --- expiry -------------------------------------------------------------
    expired_user = _make_user(client, "expired@example.com")
    s = client._Session()
    try:
        stale_raw = issue_reset_token(s, expired_user)
        row = s.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == hash_token(stale_raw)
            )
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
    finally:
        s.close()

    dead = client.post(
        f"/password/reset/{stale_raw}", data={"password": NEW_PASSWORD}, follow_redirects=False
    )
    assert dead.status_code == 400, "an expired reset link was accepted"

    client.cookies.clear()
    assert client.post(
        "/login", data={"email": "expired@example.com", "password": OLD_PASSWORD},
        follow_redirects=False,
    ).status_code == 303, "the expired link changed the password before refusing"


def test_a_forged_token_is_refused(client):
    """A token that was never issued. The `None` path, asserted directly."""
    _make_user(client, "forge@example.com")
    r = client.post(
        "/password/reset/not-a-real-token", data={"password": NEW_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert not r.cookies.get(SESSION_COOKIE)


def test_the_form_validates_the_token_before_rendering(client):
    """A dead link must not present a password form.

    Otherwise someone chooses a password, submits, and only then learns the link was dead — with
    no way to tell whether the password was the problem.
    """
    user_id = _make_user(client, "formcheck@example.com")
    raw = _mint_token(client, user_id)

    live = client.get(f"/password/reset/{raw}")
    assert live.status_code == 200
    assert 'name="password"' in live.text, "a live link does not show the form"

    dead = client.get("/password/reset/nonsense-token")
    assert dead.status_code == 400
    assert 'name="password"' not in dead.text, (
        "a dead link still renders a password form, so the failure is discovered only after "
        "someone has chosen and typed a new password"
    )


# ── A11 — the request reveals nothing ─────────────────────────────────────────

def test_no_enumeration_on_request(client, sent):
    """**A11 · G-oracle** — the two responses are equal to *each other*.

    Asserted as equality between a known and an unknown address rather than each against a
    hardcoded 200: a per-case assertion passes precisely when the two differ, which is the
    defect. And the positive twin is what stops a route that does nothing from passing —
    **mail must actually be enqueued** for the real address and not for the fake one.
    """
    _make_user(client, "real@example.com")

    known = client.post(
        "/password/reset", data={"email": "real@example.com"}, follow_redirects=False
    )
    unknown = client.post(
        "/password/reset", data={"email": "nobody-here@example.com"}, follow_redirects=False
    )

    assert known.status_code == unknown.status_code, (
        f"known={known.status_code} unknown={unknown.status_code}"
    )
    assert known.text == unknown.text, (
        "the two reset-request pages differ. Whatever that difference is, it tells a stranger "
        "which addresses have accounts"
    )

    for body in (known.text, unknown.text):
        low = body.lower()
        for leak in ("no account", "not found", "unknown email", "no user", "doesn't exist"):
            assert leak not in low, f"the page says {leak!r}"

    # **The positive twin, and without it a route that returns a page and does nothing else
    # passes everything above.** Exactly one message for the real address, none for the fake.
    assert _sent_to(sent, "real@example.com") == 1, (
        "no reset mail was sent for a real address — A11 is satisfied by a route that does "
        "nothing at all, so this is the assertion that makes it mean something"
    )
    assert _sent_to(sent, "nobody-here@example.com") == 0, (
        "mail was sent for an address with no account"
    )

    # And it carries a usable link, not an empty template. A message that arrives with a broken
    # URL is the same lockout as no message.
    body = next(m for m in sent if m["to"] == "real@example.com")["html"]
    assert "/password/reset/" in body, "the reset mail carries no link"

    # A token exists for the real user and only for them.
    s = client._Session()
    try:
        assert s.execute(
            select(func.count()).select_from(PasswordResetToken)
        ).scalar_one() == 1
    finally:
        s.close()


def test_a_google_only_address_gets_the_same_response(client, sent):
    """An account with no password has nothing to reset — and must not be identifiable.

    Distinct from the unknown-address case in the database and identical in the response. Saying
    "that account uses Google sign-in" would confirm the address exists *and* name its provider.
    """
    s = client._Session()
    try:
        s.add(User(google_sub="sub-google-reset", email="google@example.com"))
        s.commit()
    finally:
        s.close()

    google = client.post(
        "/password/reset", data={"email": "google@example.com"}, follow_redirects=False
    )
    unknown = client.post(
        "/password/reset", data={"email": "ghost@example.com"}, follow_redirects=False
    )

    assert google.status_code == unknown.status_code
    assert google.text == unknown.text, "a Google-only address answers differently"
    assert _sent_to(sent, "google@example.com") == 0, (
        "a reset link was mailed for an account that has no password"
    )


def test_the_throttled_request_looks_identical(client):
    """Step 4's second half — the reset route is throttled, invisibly.

    §6 Step 4 says the limiter is wired into "login **and** reset". G4 wired only login, so this
    lands here. Unthrottled, `/password/reset` mails a stranger on demand — a mail bomb pointed
    at any address an attacker names.

    The throttled reply must be indistinguishable from an ordinary one, or the throttle becomes
    the oracle A11 closes.
    """
    from mihomes.auth.ratelimit import EMAIL_MAX_FAILURES

    _make_user(client, "throttled@example.com")

    first = client.post(
        "/password/reset", data={"email": "unknown@example.com"}, follow_redirects=False
    )

    # Exhaust the budget on the unknown address (failures are counted for it — see
    # `record_failure`), then request again.
    for _ in range(EMAIL_MAX_FAILURES + 1):
        client.post(
            "/password/reset", data={"email": "unknown@example.com"}, follow_redirects=False
        )

    throttled = client.post(
        "/password/reset", data={"email": "unknown@example.com"}, follow_redirects=False
    )

    assert throttled.status_code == first.status_code
    assert throttled.text == first.text, (
        "a throttled reset request answers differently from an ordinary one, which re-answers "
        "the question A11 exists to refuse"
    )


# ── D8 — the mail class, which nothing functional would catch ────────────────

def test_reset_mail_is_transactional_not_lifecycle():
    """**D8/N8** — asserted on the source, because no behavioural test can see this.

    `service.py:74` suppression-checks `lifecycle` mail and returns silently. A person who
    unsubscribed from digests months ago and has now forgotten their password would receive
    nothing, forever — and the failure looks exactly like the reset feature being broken, with
    no error raised anywhere.

    Every functional test in this file passes either way, because they use a provider that does
    not consult the suppression list. So the argument is asserted where it is written, in the
    manner of `test_gateway_cleanup.py`'s coverage assertions.
    """
    import ast
    import inspect

    from mihomes.services.email import service as email_service

    src = inspect.getsource(email_service.EmailService.send_password_reset)
    tree = ast.parse(src.lstrip())

    klass = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "klass" and isinstance(kw.value, ast.Constant):
                    klass = kw.value.value

    assert klass == "transactional", (
        f"send_password_reset sends {klass!r} mail. `lifecycle` is suppression-checked "
        "(service.py:74), so an unsubscribed user would be permanently locked out of their own "
        "account with no error anywhere (D8)"
    )


def test_the_klass_scan_has_teeth():
    """The mutation check for the assertion above.

    A source scan that silently matches nothing reports success. This feeds it a method that
    sends `lifecycle` and requires it to see that.
    """
    import ast

    tree = ast.parse('def f(self):\n    self._send(to, "t", {}, klass="lifecycle")\n')
    found = [
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "klass" and isinstance(kw.value, ast.Constant)
    ]
    assert found == ["lifecycle"], "the scan cannot read a klass argument that is plainly there"


def test_a_broken_mail_provider_does_not_become_an_oracle(client, monkeypatch):
    """Found while building G5: an unconfigured provider answered differently for real addresses.

    `get_email_provider` raises `EmailAuthError` when `RESEND_API_KEY` is unset — which is the
    default configuration. Uncaught, that is a **500 for an address with an account and a 200
    for one without**, so a server that is merely misconfigured hands out the account list.

    The route catches and logs instead. The token stays minted, so a resend once the key is set
    still works.
    """
    _make_user(client, "provider@example.com")

    # The default path: no EMAIL_PROVIDER, no RESEND_API_KEY.
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    known = client.post(
        "/password/reset", data={"email": "provider@example.com"}, follow_redirects=False
    )
    unknown = client.post(
        "/password/reset", data={"email": "nobody@example.com"}, follow_redirects=False
    )

    assert known.status_code == unknown.status_code, (
        f"a broken mail provider answers {known.status_code} for a real address and "
        f"{unknown.status_code} for an unknown one — a misconfigured server leaks the account list"
    )
    assert known.status_code == 200, "the reset request 500s when mail cannot be sent"
    assert known.text == unknown.text
