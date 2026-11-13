"""Email verification at signup (2026-11-13).

The rule under test: **an unverified password user never holds a session.** Every assertion
that matters counts session rows, not status codes. A refusal page that still minted a session
would pass every status-code check here and be a bypass.

Also covered, because each is a way the feature works in a demo and fails in production:

* a mail scanner opening the link must not use it up (GET never confirms);
* a missing `RESEND_API_KEY` must not turn signup into a 500;
* the email must be sent inline, not queued: at signup there is no account, and an outbox row
  with no owner would never be sent;
* a squatted address must be recoverable by its owner (reset, or Google), and the squatter's
  password must stop working when it is.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from mihomes.auth.oidc import IdentityClaims, upsert_user
from mihomes.auth.password_identity import create_password_user
from mihomes.auth.ratelimit import reset_all as reset_ratelimit
from mihomes.auth.sessions import SESSION_COOKIE
from mihomes.models.email_verification_token import EmailVerificationToken
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User
from mihomes.services.housekeeping import prune_global
from mihomes.web.app import create_app
from mihomes.web.deps import get_db
from tests.integration.verify_helpers import confirm_email, verify_link

PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture(autouse=True)
def _clean_ratelimit():
    reset_ratelimit()
    yield
    reset_ratelimit()


@pytest.fixture
def client(_pg_engine):
    """`test_password_auth.py`'s construction: a savepoint-scoped session, rolled back after."""
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


def _user(client, email: str) -> User:
    with client._Session() as s:
        user = s.execute(select(User).where(func.lower(User.email) == email.lower())).scalar_one()
        s.expunge(user)
        return user


def _sessions(client, user_id) -> int:
    with client._Session() as s:
        return s.execute(
            select(func.count()).select_from(SessionRow).where(SessionRow.user_id == user_id)
        ).scalar_one()


def _signup(client, email: str, password: str = PASSWORD, **extra):
    return client.post(
        "/signup", data={"email": email, "password": password, **extra}, follow_redirects=False
    )


def _to(mailbox, email: str) -> int:
    return sum(1 for m in mailbox if m["to"] == email)


# ── signup ────────────────────────────────────────────────────────────────────

def test_signup_mails_a_link_and_creates_no_session(client, mailbox):
    r = _signup(client, "fresh@example.com")
    assert r.status_code == 200, r.text
    assert "Check your email" in r.text
    assert not r.cookies.get(SESSION_COOKIE)

    user = _user(client, "fresh@example.com")
    assert user.email_verified_at is None
    assert _sessions(client, user.id) == 0, "signup minted a session for an unconfirmed user"
    assert _to(mailbox, "fresh@example.com") == 1

    # Only the hash is stored: the raw token in the link must not appear in the table.
    raw = verify_link(mailbox, "fresh@example.com").rsplit("/", 1)[1]
    with client._Session() as s:
        stored = s.execute(select(EmailVerificationToken.token_hash)).scalars().all()
    assert stored and raw not in stored


def test_the_email_is_sent_inline_not_queued(client, mailbox):
    """No account exists at signup. A queued row would have no owner and never be drained,
    and on a server with scheduled jobs off nothing would drain it anyway."""
    _signup(client, "inline@example.com")
    assert _to(mailbox, "inline@example.com") == 1, "the mail did not reach the provider"
    with client._Session() as s:
        queued = s.execute(
            text("SELECT count(*) FROM email_outbox WHERE to_address = :a"),
            {"a": "inline@example.com"},
        ).scalar_one()
    assert queued == 0


def test_a_missing_provider_key_does_not_break_signup(client, monkeypatch):
    """The production default is Resend. With no key, signup must still answer, not 500."""
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    r = _signup(client, "nokey@example.com")
    assert r.status_code == 200, r.text
    assert "Check your email" in r.text


# ── the link ──────────────────────────────────────────────────────────────────

def test_opening_the_link_does_not_confirm(client, mailbox):
    """Mail scanners GET every link. If GET confirmed, the person would click a used link."""
    _signup(client, "scanner@example.com")
    path = verify_link(mailbox, "scanner@example.com").replace("http://localhost", "")
    for _ in range(3):
        assert client.get(path).status_code == 200

    user = _user(client, "scanner@example.com")
    assert user.email_verified_at is None, "a GET confirmed the address"
    assert _sessions(client, user.id) == 0

    r = confirm_email(client, mailbox, "scanner@example.com")
    assert r.status_code == 303, "the link was used up by the GETs"


def test_confirming_verifies_signs_in_and_burns_the_link(client, mailbox):
    _signup(client, "confirm@example.com")
    r = confirm_email(client, mailbox, "confirm@example.com")
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/"

    user = _user(client, "confirm@example.com")
    assert user.email_verified_at is not None
    assert _sessions(client, user.id) == 1

    client.cookies.clear()
    path = verify_link(mailbox, "confirm@example.com").replace("http://localhost", "")
    again = client.post(path, follow_redirects=False)
    assert again.status_code == 400, "the same link worked twice"
    assert _sessions(client, user.id) == 1, "a used link minted another session"


def test_every_link_dies_when_one_is_used(client, mailbox):
    """Two links (signup + a resend past the cooldown): using one kills the other."""
    _signup(client, "twolinks@example.com")
    first = verify_link(mailbox, "twolinks@example.com").replace("http://localhost", "")
    with client._Session() as s:  # age the first token past the cooldown
        s.execute(text("UPDATE email_verification_tokens SET created_at = now() - interval '5 minutes'"))
        s.commit()
    client.post("/verify-email/resend", data={"email": "twolinks@example.com"})
    assert _to(mailbox, "twolinks@example.com") == 2

    assert confirm_email(client, mailbox, "twolinks@example.com").status_code == 303
    client.cookies.clear()
    assert client.post(first, follow_redirects=False).status_code == 400


def test_an_expired_link_is_refused(client, mailbox):
    _signup(client, "expired@example.com")
    with client._Session() as s:
        s.execute(text("UPDATE email_verification_tokens SET expires_at = now() - interval '1 second'"))
        s.commit()
    path = verify_link(mailbox, "expired@example.com").replace("http://localhost", "")
    assert client.get(path).status_code == 400
    assert client.post(path, follow_redirects=False).status_code == 400
    assert _user(client, "expired@example.com").email_verified_at is None


def test_next_survives_and_a_hostile_next_is_dropped(client, mailbox):
    _signup(client, "invitee@example.com", next="/invite/abc123")
    assert "next=%2Finvite%2Fabc123" in verify_link(mailbox, "invitee@example.com")
    r = confirm_email(client, mailbox, "invitee@example.com")
    assert r.headers["location"] == "/invite/abc123"

    client.cookies.clear()
    _signup(client, "phish@example.com", next="https://evil.example/steal")
    assert "next=" not in verify_link(mailbox, "phish@example.com")


def test_a_signed_in_visitor_on_a_dead_link_goes_home(client, mailbox):
    _signup(client, "oldmail@example.com")
    confirm_email(client, mailbox, "oldmail@example.com")
    path = verify_link(mailbox, "oldmail@example.com").replace("http://localhost", "")
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


# ── sign-in before confirming ─────────────────────────────────────────────────

def test_the_right_password_before_confirming_creates_no_session(client, mailbox):
    _signup(client, "early@example.com")
    user = _user(client, "early@example.com")

    r = client.post(
        "/login", data={"email": "early@example.com", "password": PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 403, r.text
    assert "Confirm your email first" in r.text
    assert not r.cookies.get(SESSION_COOKIE)
    assert _sessions(client, user.id) == 0, "an unverified sign-in minted a session"


def test_a_wrong_password_on_an_unverified_account_reveals_nothing(client, mailbox):
    """The pending page needs the right password. A wrong one looks exactly like an unknown
    address, or the login form becomes a way to find unconfirmed accounts."""
    _signup(client, "quiet@example.com")
    unverified = client.post(
        "/login", data={"email": "quiet@example.com", "password": "wrong-passphrase-1"},
        follow_redirects=False,
    )
    unknown = client.post(
        "/login", data={"email": "nobody@example.com", "password": "wrong-passphrase-1"},
        follow_redirects=False,
    )
    assert unverified.status_code == unknown.status_code == 401
    assert "Confirm your email" not in unverified.text


# ── resend ────────────────────────────────────────────────────────────────────

def test_resend_answers_the_same_whatever_the_address(client, mailbox):
    _signup(client, "pending@example.com")
    with client._Session() as s:
        s.execute(text("UPDATE email_verification_tokens SET created_at = now() - interval '5 minutes'"))
        verified = create_password_user(s, email="done@example.com", password=PASSWORD)
        verified.email_verified_at = datetime.now(timezone.utc)
        s.commit()
    mailbox.clear()

    bodies = []
    for email in ("pending@example.com", "done@example.com", "nobody@example.com"):
        r = client.post("/verify-email/resend", data={"email": email})
        assert r.status_code == 200
        bodies.append(r.text.replace(email, "EMAIL"))
    assert bodies[0] == bodies[1] == bodies[2], "resend reveals which addresses have accounts"

    assert _to(mailbox, "pending@example.com") == 1
    assert _to(mailbox, "done@example.com") == 0, "a verified account was sent a link"
    assert _to(mailbox, "nobody@example.com") == 0


def test_resend_is_rate_limited_per_person(client, mailbox):
    _signup(client, "spam@example.com")
    for _ in range(5):
        client.post("/verify-email/resend", data={"email": "spam@example.com"})
        client.post("/login", data={"email": "spam@example.com", "password": PASSWORD})
    assert _to(mailbox, "spam@example.com") == 1, "the cooldown did not hold"


# ── squatting ────────────────────────────────────────────────────────────────

def test_a_reset_recovers_a_squatted_address(client, mailbox):
    """A squatter signed up with the owner's address. The owner resets: the link reaches the
    real inbox, the owner's password replaces the squatter's, and the account is verified."""
    _signup(client, "owner@example.com", password="the-squatters-passphrase")
    mailbox.clear()

    client.post("/password/reset", data={"email": "owner@example.com"})
    import re
    msg = next(m for m in mailbox if m["to"] == "owner@example.com")
    path = re.search(r'href="([^"]*/password/reset/[^"]+)"', msg["html"]).group(1)
    path = path.replace("http://localhost", "")
    done = client.post(path, data={"password": "the-owners-passphrase"}, follow_redirects=False)
    assert done.status_code == 303

    assert _user(client, "owner@example.com").email_verified_at is not None
    client.cookies.clear()
    reset_ratelimit()
    squat = client.post(
        "/login", data={"email": "owner@example.com", "password": "the-squatters-passphrase"},
        follow_redirects=False,
    )
    assert squat.status_code == 401, "the squatter's password still works"
    owner = client.post(
        "/login", data={"email": "owner@example.com", "password": "the-owners-passphrase"},
        follow_redirects=False,
    )
    assert owner.status_code == 303


def test_signup_does_not_overwrite_an_unverified_account(client, mailbox):
    """Overwriting would let an attacker re-sign after the owner and have the owner's click
    activate the attacker's password."""
    _signup(client, "race@example.com", password="the-owners-passphrase")
    r = _signup(client, "race@example.com", password="the-attackers-passphrase")
    assert r.status_code == 400
    assert "already exists" in r.text

    confirm_email(client, mailbox, "race@example.com")
    client.cookies.clear()
    attacker = client.post(
        "/login", data={"email": "race@example.com", "password": "the-attackers-passphrase"},
        follow_redirects=False,
    )
    assert attacker.status_code == 401


def test_google_claims_an_unverified_password_row(client):
    with client._Session() as s:
        squat = create_password_user(s, email="claimed@example.com", password=PASSWORD)
        squat_id = squat.id
        user = upsert_user(s, IdentityClaims(subject="real-owner-sub", email="Claimed@example.com"))
        s.commit()

        assert user.id == squat_id, "Google forked a second identity instead of claiming the row"
        assert user.google_sub == "real-owner-sub"
        assert user.password_hash is None, "the squatter's password survived the claim"
        assert user.email_verified_at is not None
        count = s.execute(
            select(func.count()).select_from(User)
            .where(func.lower(User.email) == "claimed@example.com")
        ).scalar_one()
    assert count == 1


def test_a_new_google_user_is_verified(client):
    with client._Session() as s:
        user = upsert_user(s, IdentityClaims(subject="brand-new-sub", email="g@example.com"))
        s.commit()
        assert user.email_verified_at is not None


# ── housekeeping ─────────────────────────────────────────────────────────────

def test_expired_links_are_pruned(client, mailbox):
    _signup(client, "prune@example.com")
    with client._Session() as s:
        counts = prune_global(s, now=datetime.now(timezone.utc) + timedelta(days=2))
        s.commit()
        left = s.execute(select(func.count()).select_from(EmailVerificationToken)).scalar_one()
    assert counts["verify_links"] >= 1
    assert left == 0
