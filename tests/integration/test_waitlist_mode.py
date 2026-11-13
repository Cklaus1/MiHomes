"""Waitlist mode — `MIHOMES_SIGNUP_MODE=waitlist` (`auth/signup_gate.py`, 2026-11-13).

What must hold with the switch on:

* a stranger cannot create an account — not by the form, not by POSTing directly, not by a first
  Google sign-in — and is pointed at the waitlist instead;
* everyone who already has an account signs in exactly as before (password and Google);
* a tester invite link admits exactly the address it was minted for;
* a team invite (`/invite/{token}`) still works end to end — the real A14 test, re-run;
* the homepage offers the waitlist, and `POST /waitlist` stores a row without revealing whether
  the address was already there.
"""

from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from mihomes.auth import signup_gate
from mihomes.models.user import User
from mihomes.models.waitlist import Waitlist
from tests.integration import test_password_auth as _pw
from tests.integration.test_auth import _FakeProvider

# The password suite's fixtures and helpers, re-used rather than copied. Assigned, not imported,
# so pytest registers the fixtures here without the names reading as unused imports.
client = _pw.client
_clean_ratelimit = _pw._clean_ratelimit
_make_user = _pw._make_user
GOOD_PASSWORD = _pw.GOOD_PASSWORD
_a14 = _pw.test_invitee_without_google

TESTER = "tester@example.com"


@pytest.fixture(autouse=True)
def _waitlist_on(monkeypatch):
    monkeypatch.setenv("MIHOMES_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv(signup_gate.SIGNUP_MODE_ENV, "waitlist")


@pytest.fixture
def provider(monkeypatch):
    fake = _FakeProvider(claims={
        "sub": "google-sub-waitlist", "email": TESTER, "email_verified": True, "name": "T",
    })
    monkeypatch.setattr("mihomes.web.routes.auth._provider", lambda: fake)
    return fake


def _users(client, email: str) -> int:
    with client._Session() as s:
        return s.execute(
            select(func.count()).select_from(User).where(func.lower(User.email) == email)
        ).scalar_one()


def _invite_query(email: str = TESTER) -> str:
    return f"email={email}&invite={signup_gate.tester_invite_token(email)}"


def _google_round_trip(client):
    client.get("/auth/google/start", follow_redirects=False)
    state = client.cookies.get("mihomes_oauth_state")
    return client.get(f"/auth/google/callback?code=good-code&state={state}", follow_redirects=False)


# ── The token ─────────────────────────────────────────────────────────────────────────────────


def test_tester_token_admits_only_its_own_address_until_it_expires():
    token = signup_gate.tester_invite_token(TESTER)
    assert signup_gate.tester_invite_valid(TESTER, token)
    assert signup_gate.tester_invite_valid("  Tester@Example.com ", token)  # normalized
    assert not signup_gate.tester_invite_valid("someone-else@example.com", token)
    expiry, _, signature = token.partition(".")
    assert not signup_gate.tester_invite_valid(TESTER, f"{int(expiry) + 86400}.{signature}")
    assert not signup_gate.tester_invite_valid(TESTER, signature)
    assert not signup_gate.tester_invite_valid(TESTER, "")
    past = int(time.time()) - 10
    assert not signup_gate.tester_invite_valid(TESTER, f"{past}.{signup_gate._sign(TESTER, past)}")


def test_mode_off_admits_everyone(monkeypatch, client):
    monkeypatch.delenv(signup_gate.SIGNUP_MODE_ENV)
    assert client.get("/signup").status_code == 200
    r = client.post("/signup", data={"email": "open@example.com", "password": GOOD_PASSWORD},
                    follow_redirects=False)
    assert r.status_code == 303


# ── Strangers ─────────────────────────────────────────────────────────────────────────────────


def test_signup_form_sends_a_stranger_to_the_waitlist(client):
    r = client.get("/signup", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/#waitlist"


def test_posting_directly_creates_no_account(client):
    r = client.post("/signup", data={"email": "stranger@example.com", "password": GOOD_PASSWORD},
                    follow_redirects=False)
    assert r.status_code == 403
    assert "invite-only" in r.text
    assert _users(client, "stranger@example.com") == 0


def test_first_google_sign_in_without_an_invite_creates_no_account(client, provider):
    r = _google_round_trip(client)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?error=")
    assert _users(client, TESTER) == 0


# ── Existing accounts ─────────────────────────────────────────────────────────────────────────


def test_existing_password_user_still_signs_in(client):
    _make_user(client, "existing@example.com")
    r = client.post("/login", data={"email": "existing@example.com", "password": GOOD_PASSWORD},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.cookies.get("mihomes_session")


def test_existing_google_user_still_signs_in(client, provider):
    with client._Session() as s:
        s.add(User(google_sub="google-sub-waitlist", email=TESTER))
        s.commit()
    r = _google_round_trip(client)
    assert r.status_code == 303
    assert not r.headers["location"].startswith("/login")
    assert client.cookies.get("mihomes_session")


# ── Tester invites ────────────────────────────────────────────────────────────────────────────


def test_tester_link_opens_the_form_and_creates_the_account(client):
    page = client.get(f"/signup?{_invite_query()}", follow_redirects=False)
    assert page.status_code == 200
    assert f'value="{TESTER}"' in page.text
    assert 'name="invite"' in page.text

    token = signup_gate.tester_invite_token(TESTER)
    r = client.post("/signup", data={"email": TESTER, "password": GOOD_PASSWORD, "invite": token},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    assert _users(client, TESTER) == 1


def test_tester_link_does_not_work_for_another_address(client):
    token = signup_gate.tester_invite_token(TESTER)
    r = client.post("/signup", data={"email": "friend@example.com", "password": GOOD_PASSWORD,
                                     "invite": token}, follow_redirects=False)
    assert r.status_code == 403
    assert _users(client, "friend@example.com") == 0


def test_tester_link_then_google_creates_the_account(client, provider):
    client.get(f"/signup?{_invite_query()}")  # parks the invite in a cookie
    assert client.cookies.get(signup_gate.INVITE_COOKIE)
    r = _google_round_trip(client)
    assert r.status_code == 303
    assert not r.headers["location"].startswith("/login"), r.headers["location"]
    assert _users(client, TESTER) == 1


def test_google_with_a_link_for_another_address_is_refused(client, provider):
    client.get(f"/signup?{_invite_query('friend@example.com')}")
    r = _google_round_trip(client)
    assert r.headers["location"].startswith("/login?error=")
    assert _users(client, TESTER) == 0


# ── Team invites ──────────────────────────────────────────────────────────────────────────────


def test_team_invite_still_works_end_to_end(client):
    """The real A14 flow — invite, signed-out landing, password signup, accept — in this mode."""
    _a14(client)


# ── The public side ───────────────────────────────────────────────────────────────────────────


def test_homepage_offers_the_waitlist_not_open_signup(client):
    page = client.get("/", headers={"accept": "text/html"})  # a browser, not an API client
    assert page.status_code == 200
    assert 'action="/waitlist"' in page.text
    assert "Start free" not in page.text
    assert 'href="/signup"' not in page.text


def test_login_page_points_new_people_at_the_waitlist(client):
    page = client.get("/login")
    assert 'href="/#waitlist"' in page.text
    assert "Create an account" not in page.text


def test_join_waitlist_stores_a_row_and_answers_the_same_every_time(client):
    first = client.post("/waitlist", data={"email": "Fan@Example.com", "name": "Fan",
                                           "num_homes": "2-3", "has_staff": "yes"})
    repeat = client.post("/waitlist", data={"email": "fan@example.com", "num_homes": "x" * 50})
    malformed = client.post("/waitlist", data={"email": "not-an-address"})
    assert first.status_code == repeat.status_code == malformed.status_code == 200
    assert first.text == repeat.text == malformed.text
    assert "on the list" in first.text

    with client._Session() as s:
        row = s.execute(select(Waitlist).where(Waitlist.email == "fan@example.com")).scalar_one()
        assert (row.name, row.num_homes, row.has_staff) == ("Fan", "2-3", True)
        assert row.confirm_sent_at is None  # no email is sent, so housekeeping keeps it
        assert s.execute(
            select(func.count()).select_from(Waitlist).where(Waitlist.email == "not-an-address")
        ).scalar_one() == 0


def test_cli_invite_prints_a_link_for_that_address():
    from urllib.parse import parse_qs, urlsplit

    from typer.testing import CliRunner

    from mihomes.cli.waitlist import app as waitlist_app

    result = CliRunner().invoke(
        waitlist_app, ["invite", "Tester@Example.com", "--base-url", "https://example.test"]
    )
    assert result.exit_code == 0, result.output
    link = next(line for line in result.output.splitlines() if line.startswith("https://"))
    parts = urlsplit(link)
    assert (parts.netloc, parts.path) == ("example.test", "/signup")
    query = parse_qs(parts.query)
    assert query["email"] == [TESTER]
    assert signup_gate.tester_invite_valid(TESTER, query["invite"][0])


def _team_invite(client) -> str:
    """A pending staff invite on a fresh Pro account — the setup A14 uses, trimmed."""
    import uuid

    from sqlalchemy import text

    from mihomes.auth.password_identity import create_password_user
    from mihomes.models.membership import Membership
    from mihomes.models.property import Property, PropertyType
    from mihomes.services import invite_service
    from mihomes.tenancy import account_context

    s = client._Session()
    try:
        owner = create_password_user(s, email="team-owner@example.com", password=GOOD_PASSWORD)
        account_id = uuid.uuid4()
        s.execute(text("INSERT INTO accounts (id, slug, name, type, plan) "
                       "VALUES (:i, :s, :s, 'household', 'pro')"),
                  {"i": account_id, "s": f"acct-{uuid.uuid4().hex[:8]}"})
        s.execute(Membership.__table__.insert().values(
            id=uuid.uuid4(), account_id=account_id, user_id=owner.id, role="owner",
            status="active"))
        with account_context(account_id):
            prop = Property(name="H", slug=f"h-{uuid.uuid4().hex[:6]}",
                            property_type=PropertyType.PRIMARY, currency="USD")
            s.add(prop)
            s.flush()
            _invite, token = invite_service.create_invite(
                s, account_id, owner.id, TESTER, "staff", property_ids=[prop.id])
        s.commit()
        return token
    finally:
        s.close()


def test_team_invitee_can_sign_up_with_google_from_the_login_page(client, provider):
    token = _team_invite(client)
    client.get(f"/login?next=/invite/{token}")  # where `/invite/{token}` sends a signed-out visitor
    r = _google_round_trip(client)
    assert r.status_code == 303
    assert r.headers["location"] == f"/invite/{token}", r.headers["location"]
    assert _users(client, TESTER) == 1


def test_a_made_up_team_token_opens_nothing(client, provider):
    client.get("/login?next=/invite/not-a-real-invite-token")
    r = _google_round_trip(client)
    assert r.headers["location"].startswith("/login?error=")
    r = client.post("/signup", data={"email": "x@example.com", "password": GOOD_PASSWORD,
                                     "next": "/invite/not-a-real-invite-token"},
                    follow_redirects=False)
    assert r.status_code == 403


def test_tester_link_survives_the_site_passcode_screen(monkeypatch, client):
    """While `MIHOMES_SITE_PASSCODE` is set a tester meets the passcode first; the invite in the
    link must still be there once they are through it."""
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "letmein")
    link = f"/signup?{_invite_query()}"
    gate = client.get(link, follow_redirects=False)
    assert gate.status_code == 303
    assert gate.headers["location"].startswith("/site-passcode")
    passed = client.post("/site-passcode", data={"passcode": "letmein", "next": link},
                         follow_redirects=False)
    assert passed.status_code == 303
    assert passed.headers["location"] == link
    page = client.get(link, follow_redirects=False)
    assert page.status_code == 200
    assert 'name="invite"' in page.text
