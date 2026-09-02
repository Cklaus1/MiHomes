"""G7 · SPEC-010 — the exit criterion (A15).

**One test, one person, one continuous session.** Every other file in this spec proves a step in
isolation; this proves they compose, which is a different claim and the only one a user makes.

    signup → onboarding → sign out → sign in → forgotten password → reset → sign in again

The seams between steps are where this can fail while every unit test stays green:

* signup mints a session, but does the *next* request see it?
* sign-out clears the cookie, but is the session row actually gone?
* the reset revokes every session — **including the one doing the resetting**, which is why the
  route re-establishes afterwards. Get the order wrong and completing a reset signs you out.
* the new password must work and the old one must not, in the same browser, on the real routes.

Deliberately no fixture shortcuts: no `create_password_user`, no `issue_reset_token`. Only HTTP
against the app, plus one read of the mail the app sent — because the reset link exists exactly
once and only in that message. Reaching into the database for the token would skip the half of
the flow most likely to be broken.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from mihomes.auth.csrf import CSRF_COOKIE
from mihomes.auth.ratelimit import reset_all as reset_ratelimit
from mihomes.auth.sessions import SESSION_COOKIE
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User
from mihomes.web.app import create_app
from mihomes.web.deps import get_db

EMAIL = "journey@example.com"
FIRST_PASSWORD = "the-first-passphrase"
SECOND_PASSWORD = "the-second-passphrase"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_ratelimit()
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    yield
    reset_ratelimit()


@pytest.fixture
def mail(monkeypatch):
    """Everything the app mails, captured at the provider boundary.

    The reset link is generated once and never stored — only its hash is. So reading it here is
    not a convenience, it is the only way to follow the flow a person actually follows.
    """
    seen: list[dict] = []
    from mihomes.services.email import console_provider

    real = console_provider.ConsoleProvider.send

    def capture(self, to, subject, html, **kw):
        seen.append({"to": to, "subject": subject, "html": html, "text": kw.get("text")})
        return real(self, to, subject, html, **kw)

    monkeypatch.setattr(console_provider.ConsoleProvider, "send", capture)
    return seen


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


def _live_sessions(client, user_id) -> int:
    s = client._Session()
    try:
        return s.execute(
            select(func.count()).select_from(SessionRow).where(SessionRow.user_id == user_id)
        ).scalar_one()
    finally:
        s.close()


def _user_id(client):
    s = client._Session()
    try:
        return s.execute(select(User.id).where(User.email == EMAIL)).scalar_one()
    finally:
        s.close()


def test_exit_criterion(client, mail):
    """**A15** — the whole journey, on the real routes, as one person in one browser."""

    # ── 1. signup ────────────────────────────────────────────────────────────
    r = client.post(
        "/signup",
        data={"email": EMAIL, "password": FIRST_PASSWORD, "name": "Journey Person"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/onboarding/", (
        "a new account did not land in the wizard"
    )
    assert client.cookies.get(SESSION_COOKIE), "signup did not sign the new user in"

    user_id = _user_id(client)
    assert _live_sessions(client, user_id) == 1

    # ── 2. onboarding is reachable, AND it finishes ──────────────────────────
    # The seam a unit test cannot see: signup mints a cookie, but the NEXT request has to be
    # authenticated by it. A 401 here would mean signup "worked" and left the user outside.
    wizard = client.get("/onboarding/", follow_redirects=False)
    assert wizard.status_code < 400, (
        f"the session signup issued does not authenticate the very next request "
        f"({wizard.status_code}) — the user is signed in and locked out simultaneously"
    )

    # **This block was missing, and a live bug shipped through the gap.** The version of this
    # test that stopped at the assertion above passed while `GET /` returned
    # 403 "No account selected" for every new user — because the wizard's FIRST page renders
    # fine for an account-less person, and the test never posted through it.
    #
    # `create_session` leaves `current_account_id` NULL and nothing was setting it, so the
    # account and membership were created and the dashboard still refused. Reaching `/` is the
    # assertion that sees it; anything short of that does not.
    made = client.post(
        "/onboarding/account",
        data={"name": "Journey Estate", "account_type": "household"},
        follow_redirects=False,
    )
    assert made.status_code == 303, made.text

    s = client._Session()
    try:
        bound = s.execute(
            select(SessionRow.current_account_id).where(SessionRow.user_id == user_id)
        ).scalar_one()
    finally:
        s.close()
    assert bound is not None, (
        "the session was not bound to the account onboarding just created, so every page will "
        "answer 403 'No account selected' — signed in and locked out at the same time"
    )

    dashboard = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert dashboard.status_code == 200, (
        f"the dashboard answers {dashboard.status_code} after onboarding created an account: "
        f"{dashboard.text[:200]}"
    )

    # ── 3. sign out ──────────────────────────────────────────────────────────
    out = client.post(
        "/signout",
        data={"csrf_token": client.cookies.get(CSRF_COOKIE)},
        follow_redirects=False,
    )
    assert out.status_code == 303, out.text
    assert _live_sessions(client, user_id) == 0, (
        "sign-out cleared the cookie but left the session row alive — anyone holding that id "
        "is still authenticated"
    )
    client.cookies.clear()

    # ── 4. sign back in with the first password ──────────────────────────────
    back = client.post(
        "/login", data={"email": EMAIL, "password": FIRST_PASSWORD}, follow_redirects=False
    )
    assert back.status_code == 303, back.text
    assert _live_sessions(client, user_id) == 1

    # **`/` again, and this is a different claim from step 2's.** That one proved *onboarding*
    # binds the account it creates. This proves *sign-in* binds an account that already exists —
    # a separate code path (`establish_session`), and the one a returning user takes every day.
    #
    # Added after a mutation survived: deleting the binding from `establish_session` left this
    # test green, because it stopped at the 303 and never asked whether the session worked.
    assert client.get("/", headers={"accept": "text/html"}, follow_redirects=False).status_code == 200, (
        "signing in succeeded but the dashboard refuses — the session was minted without being "
        "bound to the user's only account, so a returning member is locked out"
    )

    # ── 5. forget it, and ask for a reset ────────────────────────────────────
    client.cookies.clear()
    mail.clear()

    asked = client.post(
        "/password/reset", data={"email": EMAIL}, follow_redirects=False
    )
    assert asked.status_code == 200, asked.text

    messages = [m for m in mail if m["to"] == EMAIL]
    assert len(messages) == 1, f"expected one reset email, got {len(messages)}"

    # The link, taken from the message — the only place it exists.
    link = re.search(r'href="([^"]*/password/reset/[^"]+)"', messages[0]["html"])
    assert link, f"no reset link in the email: {messages[0]['html'][:400]}"
    path = link.group(1).replace("http://localhost", "")

    form = client.get(path)
    assert form.status_code == 200, "the emailed link does not open the reset form"
    assert 'name="password"' in form.text

    # ── 6. complete the reset ────────────────────────────────────────────────
    done = client.post(path, data={"password": SECOND_PASSWORD}, follow_redirects=False)
    assert done.status_code == 303, done.text

    # A13 through the front door: the session from step 4 is gone, and the only survivor is the
    # one this reset issued. **The ordering matters** — revoking after establishing would sign
    # the user out of the reset they just completed.
    assert _live_sessions(client, user_id) == 1, (
        "after the reset the user has the wrong number of live sessions; either the old one "
        "survived (A13) or the new one was revoked with it"
    )
    assert client.cookies.get(SESSION_COOKIE), (
        "completing a reset did not leave the user signed in"
    )

    # ── 7. sign in with the NEW password, and not the old one ────────────────
    client.cookies.clear()

    stale = client.post(
        "/login", data={"email": EMAIL, "password": FIRST_PASSWORD}, follow_redirects=False
    )
    assert stale.status_code == 401, "the old password still works after a reset"

    client.cookies.clear()
    reset_ratelimit()  # step 7's deliberate failure must not throttle step 7's success

    final = client.post(
        "/login", data={"email": EMAIL, "password": SECOND_PASSWORD}, follow_redirects=False
    )
    assert final.status_code == 303, f"the new password does not sign in: {final.text[:300]}"
    assert client.cookies.get(SESSION_COOKIE)

    # And the session it issued authenticates the next request, which is where step 2's seam
    # would reappear if the reset path built its session differently from signup's.
    assert client.get("/onboarding/", follow_redirects=False).status_code < 400


def test_the_reset_link_survives_a_full_round_trip_only_once(client, mail):
    """The exit path's one-way door: following the emailed link twice must not work twice.

    Distinct from A12's unit-level check — this one goes through the mail, the browser and the
    real routes, which is where a "single use" that is enforced only in a service can quietly
    stop being enforced at all.
    """
    client.post(
        "/signup",
        data={"email": "onceonly@example.com", "password": FIRST_PASSWORD},
        follow_redirects=False,
    )
    client.cookies.clear()
    mail.clear()

    client.post("/password/reset", data={"email": "onceonly@example.com"}, follow_redirects=False)
    msg = next(m for m in mail if m["to"] == "onceonly@example.com")
    path = re.search(r'href="([^"]*/password/reset/[^"]+)"', msg["html"]).group(1)
    path = path.replace("http://localhost", "")

    first = client.post(path, data={"password": SECOND_PASSWORD}, follow_redirects=False)
    assert first.status_code == 303

    client.cookies.clear()
    second = client.post(path, data={"password": "a-third-passphrase"}, follow_redirects=False)
    assert second.status_code == 400, "the emailed reset link worked a second time"

    # And the second password never took effect.
    client.cookies.clear()
    reset_ratelimit()
    assert client.post(
        "/login",
        data={"email": "onceonly@example.com", "password": "a-third-passphrase"},
        follow_redirects=False,
    ).status_code == 401


def test_sign_in_does_not_guess_between_several_accounts(client):
    """Binding happens only when there is **exactly one** membership.

    With two, picking one would drop the user into whichever estate the query happened to
    return first — and they would have no indication it was a guess. That is worse than an extra
    click, so `sole_account_for` returns None and the account picker decides.

    The positive twin is the whole rest of this file: with one membership it *does* bind, or
    every page 403s.
    """
    import uuid as _uuid

    from sqlalchemy import text as _text

    from mihomes.models.membership import Membership

    client.post(
        "/signup",
        data={"email": "twoaccounts@example.com", "password": FIRST_PASSWORD},
        follow_redirects=False,
    )
    uid = _user_id_for(client, "twoaccounts@example.com")

    # **Exactly two active memberships, and no session left over from signup.**
    #
    # A mutation caught the first version of this test being blind: it signed up (creating one
    # membership implicitly, once onboarding had run), then added two more — three, not two —
    # and reused a session that was already bound. Nothing it asserted could then change.
    s = client._Session()
    try:
        s.execute(
            _text("DELETE FROM memberships WHERE user_id = :u"), {"u": uid}
        )
        s.execute(_text("DELETE FROM sessions WHERE user_id = :u"), {"u": uid})
        for slug in (f"a-{_uuid.uuid4().hex[:6]}", f"b-{_uuid.uuid4().hex[:6]}"):
            acct = _uuid.uuid4()
            s.execute(
                _text(
                    "INSERT INTO accounts (id, slug, name, type, plan) "
                    "VALUES (:i, :s, :s, 'household', 'free')"
                ),
                {"i": acct, "s": slug},
            )
            s.execute(
                Membership.__table__.insert().values(
                    id=_uuid.uuid4(), account_id=acct, user_id=uid,
                    role="owner", status="active",
                )
            )
        s.commit()

        n = s.execute(
            _text(
                "SELECT count(*) FROM memberships WHERE user_id = :u AND status = 'active'"
            ),
            {"u": uid},
        ).scalar_one()
        assert n == 2, f"the fixture built {n} memberships, not the ambiguous two"
    finally:
        s.close()

    client.cookies.clear()
    reset_ratelimit()
    r = client.post(
        "/login",
        data={"email": "twoaccounts@example.com", "password": FIRST_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303

    s = client._Session()
    try:
        bound = s.execute(
            select(SessionRow.current_account_id)
            .where(SessionRow.user_id == uid)
            .order_by(SessionRow.created_at.desc())
            .limit(1)
        ).scalar_one()
    finally:
        s.close()

    assert bound is None, (
        f"sign-in bound account {bound} for a user with two active memberships. It guessed, "
        "and the user has no way to tell they are looking at the wrong estate"
    )


def _user_id_for(client, email: str):
    s = client._Session()
    try:
        return s.execute(select(User.id).where(User.email == email)).scalar_one()
    finally:
        s.close()
