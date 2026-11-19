"""G13 · §6 Step 13 — the account switcher (A24, D11).

D11 carries the current account in a **session field**. The known limitation is recorded in the
spec rather than discovered later — *"one browser = one current account, so two families cannot
be open side by side"* — and the choice is explicitly reversible, because *"neither choice affects
isolation: `account_id` scoping plus RLS do that regardless."*

**A24's assertion is absence, not disablement.** A greyed-out control still occupies space and
still invites the question of what it would do; §6 Step 13 says *"hidden entirely for
single-account users"*, and almost every user of this product will only ever have one account.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from mihomes.models.membership import Membership
from mihomes.models.user import User
from mihomes.services.account_switcher import (
    available_accounts,
    should_show_switcher,
    switch_account,
)


def _user(session) -> User:
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    session.flush()
    return user


def _member_of(session, user, account_id, role="owner", status="active"):
    session.add(
        Membership(
            id=uuid.uuid4(), account_id=account_id, user_id=user.id,
            role=role, status=status,
        )
    )
    session.flush()


class TestVisibility:
    def test_hidden_when_single(self, session, account_a):
        """A24 — one account means no switcher at all."""
        user = _user(session)
        _member_of(session, user, account_a)

        assert should_show_switcher(session, user.id) is False

    def test_shown_with_two(self, session, account_a, account_b):
        """The positive control. A `should_show_switcher` that returned `False`
        unconditionally would satisfy A24 perfectly and ship a switcher nobody can reach."""
        user = _user(session)
        _member_of(session, user, account_a)
        _member_of(session, user, account_b)

        assert should_show_switcher(session, user.id) is True

    def test_hidden_when_the_second_membership_is_revoked(self, session, account_a, account_b):
        """Revocation removes the account from the picker immediately.

        Otherwise an offboarded contractor keeps a visible door to an account they can no longer
        open — and clicking it would fail confusingly rather than not existing.
        """
        user = _user(session)
        _member_of(session, user, account_a)
        _member_of(session, user, account_b, status="revoked")

        assert should_show_switcher(session, user.id) is False
        assert [a.id for a in available_accounts(session, user.id)] == [account_a]

    def test_listing_is_not_filtered_to_the_current_account(
        self, session, account_a, account_b
    ):
        """**The bug this function is shaped to avoid.**

        `Membership` is `TenantOwned`, so an ORM read of it under the ambient tenant context
        returns only the account the user is currently *in* — that is, the one they are trying to
        leave. A switcher built that way lists exactly one option, always, and looks like it is
        working.
        """
        user = _user(session)
        _member_of(session, user, account_a)
        _member_of(session, user, account_b)

        found = {a.id for a in available_accounts(session, user.id)}
        assert found == {account_a, account_b}


class TestSwitching:
    def _session_row(self, session, user, account_id) -> uuid.UUID:
        from datetime import datetime, timedelta, timezone

        from mihomes.auth.sessions import hash_session_id

        session_id = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO sessions (id, session_id_hash, user_id, current_account_id, "
                "created_at, expires_at) VALUES (:id, :hash, :uid, :acct, now(), :exp)"
            ),
            {
                "id": session_id,
                "hash": hash_session_id(f"raw-{session_id}"),
                "uid": user.id,
                "acct": account_id,
                "exp": datetime.now(timezone.utc) + timedelta(days=1),
            },
        )
        return session_id

    def test_switch_changes_data(self, session, account_a, account_b):
        """D11 — the switch is written **server-side** to `sessions.current_account_id`.

        Asserting on the stored column rather than on a response is the point: every subsequent
        request resolves its tenant from this row, so if it moved, the data moved.
        """
        user = _user(session)
        _member_of(session, user, account_a)
        _member_of(session, user, account_b)
        session_id = self._session_row(session, user, account_a)

        assert switch_account(session, session_id, user.id, account_b) is True

        current = session.execute(
            text("SELECT current_account_id FROM sessions WHERE id = :id"), {"id": session_id}
        ).scalar_one()
        assert current == account_b

    def test_cannot_switch_to_an_account_you_are_not_in(self, session, account_a, account_b):
        """The account id arrives from a **form the user controls**.

        Without the server-side membership check, a user could bind any account id and every
        later request would accept it — `lookup_session` would pass, because the session says so.
        """
        user = _user(session)
        _member_of(session, user, account_a)   # not account_b
        session_id = self._session_row(session, user, account_a)

        assert switch_account(session, session_id, user.id, account_b) is False

        current = session.execute(
            text("SELECT current_account_id FROM sessions WHERE id = :id"), {"id": session_id}
        ).scalar_one()
        assert current == account_a, "a rejected switch must not move the session"

    def test_cannot_switch_to_an_account_you_were_revoked_from(
        self, session, account_a, account_b
    ):
        """Revocation closes the door, not just the signpost."""
        user = _user(session)
        _member_of(session, user, account_a)
        _member_of(session, user, account_b, status="revoked")
        session_id = self._session_row(session, user, account_a)

        assert switch_account(session, session_id, user.id, account_b) is False


class TestLastUsedAccount:
    def test_successful_switch_is_remembered(self, session, account_a, account_b):
        """D11 — *"persists `last_used_account`"*.

        `sessions.current_account_id` covers this browser; this covers the next one. Without it a
        user with two accounts is asked which they meant on every new device.
        """
        user = _user(session)
        _member_of(session, user, account_a)
        _member_of(session, user, account_b)
        session_id = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO sessions (id, session_id_hash, user_id, current_account_id, "
                "created_at, expires_at) VALUES (:id, :h, :uid, :acct, now(), "
                "now() + interval '1 day')"
            ),
            {"id": session_id, "h": f"h-{session_id}", "uid": user.id, "acct": account_a},
        )

        switch_account(session, session_id, user.id, account_b)
        session.refresh(user)
        assert user.last_used_account_id == account_b

    def test_rejected_switch_is_not_remembered(self, session, account_a, account_b):
        """A refused switch must not poison the default a future session opens at.

        Otherwise a user who mistyped — or an attacker who probed — sets where the *next* sign-in
        lands, and the failure surfaces one session later where nobody connects it to the cause.
        """
        user = _user(session)
        _member_of(session, user, account_a)
        session_id = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO sessions (id, session_id_hash, user_id, current_account_id, "
                "created_at, expires_at) VALUES (:id, :h, :uid, :acct, now(), "
                "now() + interval '1 day')"
            ),
            {"id": session_id, "h": f"h-{session_id}", "uid": user.id, "acct": account_a},
        )

        assert switch_account(session, session_id, user.id, account_b) is False
        session.refresh(user)
        assert user.last_used_account_id is None
