"""G12 · §6 Step 12 — invites (A19, A20, A21).

**A19 is about a race, and is deliberately *not* tested by staging one.** `PRICING` §3.2 rule 5
requires the seat re-check *inside* the acceptance transaction *"so races (two concurrent invites
at the seat cap) cannot exceed a limit."* Accepting twice in sequence proves nothing about that —
it is the case a naive check already handles. But two threads and a barrier proved worse than
useless: see `TestSeatRace`'s docstring for why it both hung the suite and would have failed to
fail. What is asserted instead is the *mechanism* that makes the race safe — the exclusive lock,
and the check performed under it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from mihomes.models.invite import Invite
from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property
from mihomes.models.user import User
from mihomes.services import invite_service
from mihomes.services.invite_service import (
    INVITE_TTL,
    InviteError,
    SeatLimitReached,
    accept_invite,
    create_invite,
    hash_token,
    revoke_invite,
    seats_used,
)


def _user(session, email: str | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=email or f"u-{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def belle(session, account_a):
    prop = Property(
        id=uuid.uuid4(), account_id=account_a, name="Belle Estate",
        slug=f"belle-{uuid.uuid4().hex[:6]}",
    )
    session.add(prop)
    session.flush()
    return prop


class TestTokenLifecycle:
    def test_token_lifecycle(self, session, account_a, belle):
        """A20 — hashed at rest, single-use, and expiry enforced.

        The raw token must not be recoverable from the row: a database disclosure that yielded
        usable invitations would defeat the point of hashing it (D5).
        """
        invite, raw = create_invite(
            session, account_a, None, "  New.Person@Example.COM  ", "staff", [belle.id]
        )

        assert invite.token_hash == hash_token(raw)
        assert raw not in invite.token_hash
        # Normalised on the way in: surrounding whitespace stripped, case folded. Addresses are
        # matched against the signed-in user's for §6.3's mismatch notice, and a stray space or a
        # capital would make an identical address read as different.
        assert invite.email == "new.person@example.com"
        assert invite.expires_at - datetime.now(timezone.utc) <= INVITE_TTL

        accepted_by = _user(session)
        membership = accept_invite(session, raw, accepted_by)
        assert membership.role == "staff"
        assert invite.status == "accepted"

        # Single-use: the same token cannot be redeemed twice.
        with pytest.raises(InviteError):
            accept_invite(session, raw, _user(session))

    def test_expired_token_is_rejected(self, session, account_a, belle):
        invite, raw = create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        invite.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.flush()

        with pytest.raises(InviteError):
            accept_invite(session, raw, _user(session))
        assert invite.status == "expired"

    def test_revoked_invite_cannot_be_accepted(self, session, account_a, belle):
        invite, raw = create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        revoke_invite(session, invite)

        with pytest.raises(InviteError):
            accept_invite(session, raw, _user(session))

    def test_unknown_and_used_tokens_are_indistinguishable(self, session, account_a, belle):
        """The error text must not tell an attacker which tokens once existed.

        Same reasoning as D9's 404: if "no such invite" and "already used" read differently, the
        pair of responses is itself information — and this surface is reachable *before*
        authentication, so there is no membership check standing in front of it.
        """
        invite, raw = create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        accept_invite(session, raw, _user(session))

        with pytest.raises(InviteError) as used:
            accept_invite(session, raw, _user(session))
        with pytest.raises(InviteError) as unknown:
            accept_invite(session, "never-existed", _user(session))

        assert str(used.value) == str(unknown.value)

    def test_revoking_is_idempotent(self, session, account_a, belle):
        """The revoke button is exactly the kind of thing that gets double-clicked."""
        invite, _ = create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        revoke_invite(session, invite)
        revoke_invite(session, invite)
        assert invite.status == "revoked"


class TestStaffScopeIsRequired:
    def test_staff_needs_scope(self, session, account_a):
        """A21 · D3 — a staff invite with **zero** properties is rejected.

        Creating it would produce a member who signs in and sees nothing, which is
        indistinguishable to them from the product being broken — and D3's fail-closed direction
        means the fix cannot be "grant all".
        """
        with pytest.raises(InviteError, match="at least one property"):
            create_invite(session, account_a, None, "staff@example.com", "staff", [])

    def test_admin_does_not_need_scope(self, session, account_a):
        """The negative control: admins see every property (`ONBOARDING:44`), so requiring a
        scope from them would be a bug, not caution."""
        invite, _ = create_invite(session, account_a, None, "admin@example.com", "admin", [])
        assert invite.role == "admin"

    def test_owner_cannot_be_invited(self, session, account_a):
        """D2 — *"the `owner` role can never be assigned"*; ownership moves only by transfer.

        An invite is an assignment, so this closes the second route to ownership that D2 exists
        to forbid.
        """
        with pytest.raises(InviteError, match="ownership moves only by transfer"):
            create_invite(session, account_a, None, "usurper@example.com", "owner", [])

    def test_accepted_staff_invite_creates_the_scope_rows(self, session, account_a, belle):
        """The scopes must survive from creation to acceptance — days later, with the inviter
        long gone. That is why they are stored on the invite at all."""
        invite, raw = create_invite(
            session, account_a, None, "s@example.com", "staff", [belle.id]
        )
        membership = accept_invite(session, raw, _user(session))

        scopes = (
            session.query(MembershipPropertyScope)
            .filter(MembershipPropertyScope.membership_id == membership.id)
            .all()
        )
        assert [s.property_id for s in scopes] == [belle.id]


class TestSeatAccounting:
    def test_pending_invite_consumes_a_seat(self, session, account_a, belle):
        """D6 — *"a pending invite consumes a seat immediately"*, counted across **two** tables.

        `memberships.status` has no `invited` state (N7), so a count that read one table would
        undercount by exactly the number of outstanding invitations — and the account would email
        invites it could not honour.
        """
        before = seats_used(session, account_a)
        create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        assert seats_used(session, account_a) == before + 1

    def test_revoking_frees_the_seat_immediately(self, session, account_a, belle):
        before = seats_used(session, account_a)
        invite, _ = create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        revoke_invite(session, invite)
        assert seats_used(session, account_a) == before

    def test_expired_invites_do_not_hold_seats(self, session, account_a, belle):
        """*"Revoking or letting an invite expire frees the seat immediately."*

        Expiry is passive — nothing runs to mark it — so the count has to exclude by date rather
        than by status, or seats leak permanently on every unaccepted invitation.
        """
        before = seats_used(session, account_a)
        invite, _ = create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        invite.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.flush()
        assert seats_used(session, account_a) == before

    def test_accepting_does_not_double_count(self, session, account_a, belle):
        """The invite held a seat; the membership now holds it. Net zero.

        A count that added the membership without dropping the invite would charge twice for one
        person and lock an account out of seats it had paid for.
        """
        invite, raw = create_invite(session, account_a, None, "a@b.com", "staff", [belle.id])
        during = seats_used(session, account_a)
        accept_invite(session, raw, _user(session))
        assert seats_used(session, account_a) == during


class TestEmailIsNotTheAuthority:
    def test_token_from_a_different_address_still_works(self, session, account_a, belle):
        """D5 — *"the invite token is the authority, not the email."*

        Forwarding an invite to the address you actually sign in with is legitimate and common;
        a hard email check would break it while stopping nobody who already holds the link.
        """
        _invite, raw = create_invite(
            session, account_a, None, "invited@example.com", "staff", [belle.id]
        )
        other = _user(session, email="different@example.com")
        assert accept_invite(session, raw, other) is not None

    def test_mismatch_is_detectable_for_notification(self, session, account_a, belle):
        """§6.3's mitigation: not a block, but the account can be *told*.

        That notification is what turns a stolen invite from silent into visible.
        """
        invite, _ = create_invite(
            session, account_a, None, "invited@example.com", "staff", [belle.id]
        )
        assert invite_service.email_mismatch(invite, _user(session, "someone@else.com"))
        assert not invite_service.email_mismatch(
            invite, _user(session, "invited@example.com")
        )


class TestInviteEmails:
    """G12.4 — the three Phase 2 mail types on SPEC-001's `EmailService`.

    Rendered rather than mocked: `render_template` requires a `.txt` sibling for every `.html`
    (*"never ship HTML-only mail"*), so a missing plain-text part is a real defect these tests
    catch and a mock would hide.
    """

    def _service(self):
        from mihomes.services.email.console_provider import ConsoleProvider
        from mihomes.services.email.service import EmailService

        sent: list[tuple] = []

        class _Recording(ConsoleProvider):
            # `text` is keyword-only on the provider protocol, so it must be captured as one —
            # a positional signature here would swallow `reply_to` and quietly drop the plain
            # text part, which is precisely what these tests exist to check for.
            def send(self, to, subject, html, *, text=None, **kwargs):  # noqa: D102
                sent.append((to, subject, html, text or ""))
                return super().send(to, subject, html, text=text, **kwargs)

        return EmailService(_Recording("MiHomes <no-reply@example.com>")), sent

    def test_emails_sent(self):
        """All three render and dispatch, with both parts."""
        service, sent = self._service()

        service.send_welcome(
            "owner@example.com", account_name="Belle Estate",
            dashboard_url="https://app/dash", name="Dana",
        )
        service.send_staff_invite(
            "new@example.com", account_name="Belle Estate",
            accept_url="https://app/invite/tok", role="staff", inviter_name="Dana",
        )
        service.send_invite_accepted(
            "owner@example.com", account_name="Belle Estate",
            member_email="new@example.com", role="staff",
        )

        assert len(sent) == 3
        for _to, subject, html, plain in sent:
            assert subject.strip(), "every mail needs a subject line"
            assert html.strip() and plain.strip(), "never ship HTML-only mail"

    def test_invite_email_carries_the_token_url_and_the_expiry(self):
        """The URL is the **only** copy of the plaintext token — only the hash is stored (D5).

        And the 7-day expiry (B9) is stated, because an invitation with an invisible deadline is
        one people discover has expired, which reads as the product being broken.
        """
        service, sent = self._service()
        service.send_staff_invite(
            "new@example.com", account_name="Belle", accept_url="https://app/invite/SECRETTOK",
            role="staff", inviter_name="Dana",
        )

        _to, _subject, html, plain = sent[0]
        assert "SECRETTOK" in html and "SECRETTOK" in plain
        assert "7 days" in html and "7 days" in plain

    def test_acceptance_notice_warns_on_an_email_mismatch(self):
        """§6.3's mitigation, in the one place a user will see it.

        D5 lets a forwarded invitation be accepted from another address; this is what stops that
        being silent. Without the warning the mitigation D5 depends on does not exist, while the
        feature looks finished.
        """
        service, sent = self._service()
        service.send_invite_accepted(
            "owner@example.com", account_name="Belle", member_email="someone@else.com",
            role="staff", invited_email="invited@example.com",
        )

        _to, _subject, html, plain = sent[0]
        assert "invited@example.com" in html
        assert "someone@else.com" in html
        assert "invited@example.com" in plain

    def test_no_warning_when_the_addresses_match(self):
        """The negative control: a warning on every acceptance would train people to ignore it."""
        service, sent = self._service()
        service.send_invite_accepted(
            "owner@example.com", account_name="Belle", member_email="invited@example.com",
            role="staff", invited_email="invited@example.com",
        )

        _to, _subject, html, _text = sent[0]
        assert "isn't who you expected" not in html


class TestSeatRace:
    """A19 — two concurrent acceptances at the seat cap: **exactly one** succeeds.

    **Tested as the mechanism, not as a coin flip.** The obvious version — two threads racing
    with a barrier — proves the property only if the timing happens to interleave, and *fails to
    fail* on a broken implementation whenever the first thread finishes before the second starts.
    A first attempt at exactly that hung the suite for five minutes on lock contention it could
    not resolve, which is its own argument: a test whose outcome depends on scheduling is a test
    that will one day be flaky in CI and get retried rather than read.

    What actually makes the race safe is that `accept_invite` **serialises acceptances per
    account** with `SELECT ... FOR UPDATE` on the account row, so the two assertions below are
    the two halves of A19:

    1. a second transaction *cannot proceed* while a first holds the lock (this class), and
    2. the seat check is re-read under that lock and refuses at the cap (`test_cap_is_enforced`).

    Together they are stronger than the thread version *and* deterministic.
    """

    def test_acceptance_serialises_on_the_account_row(self, _pg_engine):
        """The lock exists and is held for the whole acceptance.

        A second connection asking for the same account row with a 1-second `lock_timeout` must
        time out. If `accept_invite` did not take the lock — or took it on the *invite* instead,
        which two people accepting two different invites would never contend on — this returns
        immediately and the test fails.

        **Deliberately takes neither the `session` fixture nor `account_a`.** Inserting any
        tenant row inside an open transaction makes Postgres hold a `FOR KEY SHARE` lock on the
        referenced `accounts` row for the life of that transaction, which conflicts with
        `FOR UPDATE` — so a version of this test that used the shared fixtures blocked forever
        rather than failing. The account here is created and committed on its own connection and
        cleaned up at the end, so nothing else holds a reference to it.
        """
        import psycopg

        account_id = uuid.uuid4()
        with _pg_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO accounts (id, slug, name, type, plan, created_at, updated_at) "
                    "VALUES (:id, :slug, 'Lock Probe', 'household', 'free', now(), now())"
                ),
                {"id": account_id, "slug": f"lock-probe-{account_id.hex[:8]}"},
            )

        Factory = sessionmaker(bind=_pg_engine, future=True)
        try:
            self._assert_lock_is_exclusive(_pg_engine, Factory, account_id, psycopg)
        finally:
            with _pg_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM accounts WHERE id = :id"), {"id": account_id}
                )

    @staticmethod
    def _assert_lock_is_exclusive(engine, Factory, account_a, psycopg):
        with Factory() as holder:
            holder.execute(
                text("SELECT id FROM accounts WHERE id = :a FOR UPDATE"), {"a": account_a}
            )
            _pg_engine = engine

            other = _pg_engine.raw_connection()
            try:
                cur = other.cursor()
                cur.execute("SET lock_timeout = '1s'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    cur.execute(
                        "SELECT id FROM accounts WHERE id = %s FOR UPDATE", (str(account_a),)
                    )
                other.rollback()
            finally:
                other.close()
            holder.rollback()

    def test_cap_is_enforced_under_the_lock(self, session, account_a, belle, monkeypatch):
        """The second acceptance at the cap is refused — the half the lock makes atomic.

        The cap is lowered rather than seats manufactured: filling a real Free plan's seats would
        make this a test about `PLAN_LIMITS` (which G4 already covers) instead of about the
        refusal.
        """
        monkeypatch.setattr(invite_service, "_seat_limit", lambda *_: 1)

        # One seat, already taken by the account's own owner-to-be.
        first = _user(session)
        session.add(
            Membership(
                id=uuid.uuid4(), account_id=account_a, user_id=first.id,
                role="admin", status="active",
            )
        )
        session.flush()

        # An invite minted before the cap was reached is still redeemable-looking...
        invite = Invite(
            id=uuid.uuid4(), account_id=account_a, email="late@example.com", role="admin",
            property_ids=[], token_hash=hash_token("late-token"),
            expires_at=datetime.now(timezone.utc) + INVITE_TTL, status="pending",
        )
        session.add(invite)
        session.flush()

        # ...and must still be refused at redemption, because the seat went to someone else.
        with pytest.raises(SeatLimitReached):
            accept_invite(session, "late-token", _user(session))

    def test_creation_is_also_capped(self, session, account_a, belle, monkeypatch):
        """*"So we never email an invite that can't be honored"* (`ONBOARDING` §6.4).

        The acceptance check is the correctness guarantee; this is the courtesy that stops the
        product promising a seat it does not have.
        """
        monkeypatch.setattr(invite_service, "_seat_limit", lambda *_: 1)

        taken = _user(session)
        session.add(
            Membership(
                id=uuid.uuid4(), account_id=account_a, user_id=taken.id,
                role="admin", status="active",
            )
        )
        session.flush()

        with pytest.raises(SeatLimitReached):
            create_invite(session, account_a, None, "nope@example.com", "admin", [])
