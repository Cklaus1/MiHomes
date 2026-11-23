"""Settings, Emails: who gets which email (2026-11-23).

Founder's rules, each asserted here:
- weekly report: owner and admins by default, **never** staff (it shows spending);
- chat digest: everyone by default; onboarding tips: the owner;
- each member may turn off what they were given, and cannot turn on what they were not;
- required emails (security, billing) cannot be turned off from the page;
- an unsubscribe-link click can be undone from the page, a spam complaint cannot.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from mihomes.auth.csrf import CSRF_COOKIE
from mihomes.ids import new_id
from mihomes.models.membership import Membership
from mihomes.models.user import User
from mihomes.services.email import preferences as prefs


def _csrf(client) -> str:
    """The token sign-in issues; these sessions are created directly, so set it as sign-in would."""
    client.cookies.set(CSRF_COOKIE, "test-csrf-token")
    return "test-csrf-token"


def _member(session, account_id, role: str, *, name: str | None = None, email=None):
    user = User(id=new_id(), google_sub=f"sub-{role}-{uuid.uuid4().hex[:8]}",
                email=email or f"{role}-{uuid.uuid4().hex[:6]}@example.com", name=name)
    session.add(user)
    session.flush()
    m = Membership(id=new_id(), account_id=account_id, user_id=user.id, role=role, status="active")
    session.add(m)
    session.flush()
    return m


# ── the rules ─────────────────────────────────────────────────────────────────────────────────

def test_defaults(session, account_a):
    owner = _member(session, account_a, "owner")
    admin = _member(session, account_a, "admin")
    staff = _member(session, account_a, "staff")

    assert prefs.wants(session, owner, "weekly_report")
    assert prefs.wants(session, admin, "weekly_report")
    assert not prefs.wants(session, staff, "weekly_report")
    assert all(prefs.wants(session, m, "chat_digest") for m in (owner, admin, staff))
    assert prefs.wants(session, owner, "onboarding_tips")
    assert not prefs.wants(session, admin, "onboarding_tips")


def test_staff_can_never_be_given_the_weekly_report(session, account_a):
    staff = _member(session, account_a, "staff")
    prefs.set_team_roles(session, "weekly_report", ["owner", "admin", "staff"])
    assert prefs.team_roles(session, "weekly_report") == ("owner", "admin")
    assert not prefs.wants(session, staff, "weekly_report")

    # Even a value written straight into the settings table is filtered on read.
    from mihomes.services.config_service import set_config
    set_config(session, "email.roles.weekly_report", "owner,staff")
    assert prefs.team_roles(session, "weekly_report") == ("owner",)


def test_a_member_can_turn_off_what_they_were_given(session, account_a):
    admin = _member(session, account_a, "admin")
    prefs.set_personal_switch(session, admin, "weekly_report", False)
    assert not prefs.wants(session, admin, "weekly_report")
    prefs.set_personal_switch(session, admin, "weekly_report", True)
    assert prefs.wants(session, admin, "weekly_report")


def test_the_team_setting_overrides_a_personal_on(session, account_a):
    admin = _member(session, account_a, "admin")
    prefs.set_personal_switch(session, admin, "weekly_report", True)
    prefs.set_team_roles(session, "weekly_report", ["owner"])
    assert not prefs.wants(session, admin, "weekly_report")


def test_chat_digest_switch_is_the_existing_column(session, account_a):
    """The chat page and the Emails page edit one value, so they can never disagree."""
    staff = _member(session, account_a, "staff")
    prefs.set_personal_switch(session, staff, "chat_digest", False)
    stored = session.execute(text(
        "SELECT email_digest FROM chat_notify_prefs WHERE membership_id = :m"), {"m": staff.id}
    ).scalar_one()
    assert stored is False
    assert not prefs.wants(session, staff, "chat_digest")


def test_required_emails_cannot_be_switched_off(session, account_a):
    owner = _member(session, account_a, "owner")
    with pytest.raises(ValueError):
        prefs.set_personal_switch(session, owner, "security", False)
    with pytest.raises(ValueError):
        prefs.set_team_roles(session, "billing", [])


def test_recipients_names_everyone_who_wants_it(session, account_a):
    _member(session, account_a, "owner", name="Millena", email="owner@x.example")
    admin = _member(session, account_a, "admin", name="Ana", email="admin@x.example")
    _member(session, account_a, "staff", name="Sam", email="staff@x.example")
    got = dict(prefs.recipients(session, account_a, "weekly_report"))
    assert got == {"owner@x.example": "Millena", "admin@x.example": "Ana"}

    prefs.set_personal_switch(session, admin, "weekly_report", False)
    assert dict(prefs.recipients(session, account_a, "weekly_report")) == {
        "owner@x.example": "Millena"}


# ── the page ──────────────────────────────────────────────────────────────────────────────────

def test_staff_see_their_emails_but_not_the_team_section(web_client_as):
    staff = web_client_as("staff")
    page = staff.get("/settings/emails")
    assert page.status_code == 200
    assert "Emails you get" in page.text
    assert "What your team gets" not in page.text
    assert "Weekly estate report" not in page.text, "staff were offered the weekly report"
    assert "Unread chat messages" in page.text
    tabs = page.text.split('aria-label="Settings pages"', 1)[1].split("</nav>", 1)[0]
    assert ">You</a>" in tabs and ">Emails</a>" in tabs and ">Estate</a>" not in tabs


def test_owner_sees_both_sections(web_client_as):
    owner = web_client_as("owner")
    page = owner.get("/settings/emails").text
    assert "Emails you get" in page and "What your team gets" in page
    assert 'value="weekly_report.staff"' not in page, "staff checkbox offered for the report"
    assert 'value="chat_digest.staff"' in page


def test_saving_my_emails_and_the_csrf_guard(web_client_as):
    admin = web_client_as("admin")
    admin.get("/settings/emails")
    bad = admin.post("/settings/emails/mine", data={"csrf_token": "wrong", "on": ["chat_digest"]},
                     follow_redirects=False)
    assert bad.status_code == 403

    ok = admin.post("/settings/emails/mine",
                    data={"csrf_token": _csrf(admin),
                          "on": ["chat_digest", "onboarding_tips"]},
                    follow_redirects=False)
    assert ok.status_code == 303
    page = admin.get(ok.headers["location"]).text
    assert "Email choices saved" in page

    def check(s):
        m = s.get(Membership, admin.membership_id)
        assert not prefs.wants(s, m, "weekly_report"), "unticked report stayed on"
        assert prefs.wants(s, m, "chat_digest")
        assert not prefs.wants(s, m, "onboarding_tips"), "an admin switched on owner-only tips"
    web_client_as.seed(check)


def test_only_owners_and_admins_change_the_team_settings(web_client_as):
    staff = web_client_as("staff")
    staff.get("/settings/emails")
    r = staff.post("/settings/emails/team",
                   data={"csrf_token": _csrf(staff), "roles": []},
                   follow_redirects=False)
    assert r.status_code in (403, 404)

    owner = web_client_as("owner")
    owner.get("/settings/emails")
    r = owner.post("/settings/emails/team",
                   data={"csrf_token": _csrf(owner),
                         "roles": ["weekly_report.owner", "weekly_report.staff",
                                   "chat_digest.owner", "chat_digest.admin"]},
                   follow_redirects=False)
    assert r.status_code == 303

    def check(s):
        assert prefs.team_roles(s, "weekly_report") == ("owner",)
        assert prefs.team_roles(s, "chat_digest") == ("owner", "admin")
        assert prefs.team_roles(s, "onboarding_tips") == ()
    web_client_as.seed(check)


def test_undoing_an_unsubscribe_but_never_a_complaint(web_client_as):
    from mihomes.models.email_suppression import EmailSuppression

    owner = web_client_as("owner")
    email = web_client_as.connection.execute(
        text("SELECT email FROM users WHERE id = :u"), {"u": owner.user_id}).scalar_one()
    web_client_as.connection.execute(text(
        "INSERT INTO email_suppressions (id, address, reason, suppressed_at) "
        "VALUES (:id, :a, 'unsubscribe', now())"), {"id": uuid.uuid4(), "a": email.lower()})

    page = owner.get("/settings/emails").text
    assert "Turn optional emails back on" in page
    r = owner.post("/settings/emails/resume",
                   data={"csrf_token": _csrf(owner)}, follow_redirects=False)
    assert r.status_code == 303
    left = web_client_as.connection.execute(
        select(EmailSuppression.id).where(EmailSuppression.address == email.lower())).first()
    assert left is None

    web_client_as.connection.execute(text(
        "INSERT INTO email_suppressions (id, address, reason, suppressed_at) "
        "VALUES (:id, :a, 'complaint', now())"), {"id": uuid.uuid4(), "a": email.lower()})
    assert "Turn optional emails back on" not in owner.get("/settings/emails").text
    owner.post("/settings/emails/resume", data={"csrf_token": _csrf(owner)})
    assert web_client_as.connection.execute(
        select(EmailSuppression.id).where(EmailSuppression.address == email.lower())
    ).first() is not None, "a spam complaint was undone"
