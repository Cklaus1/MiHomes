"""G3 · §6 Step 2 — A33: every privileged action and **every deny** writes a real actor.

F6: the audit table is not greenfield. `models/audit_log.py` exists and is already `TenantOwned`;
what was missing is a real actor — `AuditLog.actor` defaults to `"admin"` and the bot never
overrides it, so every current call site records a fiction.

**The durability test is the one that matters.** FastAPI's `get_db` rolls back on any exception,
and `HTTPException` is an exception — so a deny audit written through the request's session is
discarded by the very mechanism that reports the denial. A33 would then be false in production
while passing any test that asserts inside the transaction.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from mihomes.authz.audit import NO_TARGET, audit_write
from mihomes.authz.permissions import require_permission
from mihomes.models.audit_log import AuditLog
from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property
from mihomes.models.user import User
from mihomes.web.deps import RequestPrincipal


def _principal(session, account_id, role: str, scoped_to=()) -> RequestPrincipal:
    user = User(
        id=uuid.uuid4(), google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    session.flush()
    membership = Membership(
        id=uuid.uuid4(), account_id=account_id, user_id=user.id,
        role=role, status="active",
    )
    session.add(membership)
    session.flush()
    for prop in scoped_to:
        session.add(
            MembershipPropertyScope(
                id=uuid.uuid4(), account_id=account_id,
                membership_id=membership.id, property_id=prop.id,
            )
        )
    session.flush()
    return RequestPrincipal(
        user_id=user.id, account_id=account_id,
        membership_id=membership.id, role=role,
    )


@pytest.fixture
def audit_factory(_pg_engine, account_a):
    """A session factory on a **genuinely separate connection**, and that is the point.

    The obvious fixture — `sessionmaker(bind=session.get_bind())` — binds to the test's own
    connection, so the "independent" write lands in a savepoint inside the test transaction and
    is discarded by the very rollback it is supposed to survive. That fixture makes
    `TestDenyAuditSurvivesRollback` fail against *correct* code, which is worse than useless: it
    would push someone to "fix" `audit_deny` by removing the independence that makes A33 true.

    Binding to the engine reproduces production: a separate connection, its own transaction, its
    own commit. The rows therefore outlive the test transaction, so this fixture cleans them up
    itself rather than relying on the outer rollback.
    """
    factory = sessionmaker(bind=_pg_engine, future=True)
    yield factory
    with _pg_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM audit_log WHERE account_id = :account_id"),
            {"account_id": account_a},
        )


def _audit_rows(session, action: str | None = None) -> list[AuditLog]:
    stmt = select(AuditLog)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    return list(session.execute(stmt).scalars())


class TestDenyAuditing:
    def test_denies_and_actor(self, session, account_a, audit_factory):
        """A33 — a refusal writes a row naming the **real** actor, not `"admin"`."""
        staff = _principal(session, account_a, "staff")

        with pytest.raises(HTTPException):
            require_permission(
                session, staff, "finance.view", audit_session_factory=audit_factory
            )

        rows = _audit_rows(session, action="deny")
        assert len(rows) == 1
        row = rows[0]
        assert row.actor == str(staff.user_id), (
            "the audit row must name the acting user, not the legacy 'admin' default (F6)"
        )
        assert row.changes["attempted"] == "finance.view"
        assert row.changes["role"] == "staff"
        assert row.account_id == account_a

    def test_every_denial_path_audits(self, session, account_a, audit_factory):
        """Each distinct refusal reason must produce a row.

        Written as a sweep rather than one case because the refusals are scattered across five
        branches, and a branch that raises without auditing is invisible to a test that only
        exercises the first one.
        """
        belle = Property(
            id=uuid.uuid4(), account_id=account_a, name="Belle",
            slug=f"belle-{uuid.uuid4().hex[:6]}",
        )
        session.add(belle)
        session.flush()

        staff = _principal(session, account_a, "staff")
        admin = _principal(session, account_a, "admin")

        cases = [
            (staff, "finance.view", None),          # role denial
            (staff, "task.manage", None),           # SCOPED item with no target
            (staff, "task.manage", belle),          # out of scope
            (admin, "account.transfer", None),      # owner-only
            (admin, "task.mange", None),            # unknown action (typo)
            (admin, "property.view", uuid.uuid4()),  # nonexistent target
        ]
        for principal, action, target in cases:
            with pytest.raises(HTTPException):
                require_permission(
                    session, principal, action, target,
                    audit_session_factory=audit_factory,
                )

        assert len(_audit_rows(session, action="deny")) == len(cases), (
            "every refusal branch must audit — one that raises without auditing is a silent "
            "denial (A33, §9.4's closing paragraph)"
        )

    def test_permitted_action_writes_no_deny_row(self, session, account_a, audit_factory):
        """The negative control. A `_deny` helper wired into the *success* path too would
        satisfy every assertion above while filling the log with fictional refusals."""
        owner = _principal(session, account_a, "owner")
        require_permission(
            session, owner, "member.manage", audit_session_factory=audit_factory
        )
        assert _audit_rows(session, action="deny") == []

    def test_deny_without_target_uses_the_sentinel(self, session, account_a, audit_factory):
        """`entity_id` is NOT NULL, so an account-level denial still needs a value.

        The sentinel is used rather than the account id, which would be indistinguishable from a
        genuine account-level audit row.
        """
        staff = _principal(session, account_a, "staff")
        with pytest.raises(HTTPException):
            require_permission(
                session, staff, "finance.view", audit_session_factory=audit_factory
            )
        assert _audit_rows(session, action="deny")[0].entity_id == NO_TARGET


class TestDenyAuditSurvivesRollback:
    """**The property the two-transaction design exists for.**

    A deny audit written through the request session is rolled back by `get_db`'s
    `except Exception: s.rollback()` — the same path that turns the `HTTPException` into a 403.
    """

    def test_deny_audit_is_not_lost_when_the_caller_rolls_back(
        self, session, account_a, audit_factory
    ):
        staff = _principal(session, account_a, "staff")

        with pytest.raises(HTTPException):
            require_permission(
                session, staff, "finance.view", audit_session_factory=audit_factory
            )

        # Simulate what `get_db` does when the route raises.
        session.rollback()

        assert _audit_rows(session, action="deny"), (
            "the deny audit must survive the request transaction's rollback — otherwise A33's "
            "'every deny writes a row' is false in production while passing in-transaction tests"
        )


class TestExistingCallSitesGetARealActor:
    """A33's other half — the 73 pre-existing `record_change` call sites.

    F6: *"every current call site writes a fictional actor."* The services making those calls do
    not know who is acting, and threading a parameter through 20 files would be a large,
    error-prone sweep that a missed call site would silently defeat. The request already knows —
    `current_user` is bound per request by `require_authenticated` and per command by the CLI —
    so resolving it at the point of writing fixes all 73 at once.
    """

    def test_record_change_uses_the_context_user(self, session, account_a):
        from mihomes.services.audit import record_change
        from mihomes.tenancy import account_context

        acting_user = uuid.uuid4()
        with account_context(account_a, acting_user):
            entry = record_change(
                session, entity_type="task", entity_id=uuid.uuid4(), action="update"
            )
        assert entry.actor == str(acting_user), (
            "an audited change made inside a request must name the requesting user"
        )

    def test_unattended_path_is_labelled_system_not_admin(self, session, account_a):
        """The fallback must be honest.

        `"admin"` was a guess that reads like a real principal in the log; `"system"` is true and
        is visibly not a user id, so an unattributed write cannot be mistaken for a human one.
        """
        from mihomes.services.audit import record_change

        entry = record_change(
            session, entity_type="task", entity_id=uuid.uuid4(), action="update"
        )
        assert entry.actor == "system"
        assert entry.actor != "admin"

    def test_explicit_actor_still_wins(self, session, account_a):
        """Paths that genuinely act for someone else keep their override."""
        from mihomes.services.audit import record_change

        entry = record_change(
            session, entity_type="task", entity_id=uuid.uuid4(), action="update",
            actor="telegram:12345",
        )
        assert entry.actor == "telegram:12345"


class TestAuditWrite:
    def test_actor_has_no_default(self, session, account_a):
        """F6's root cause, pinned. `AuditLog.actor` defaults to `"admin"` at the model, so a
        wrapper with its own default would quietly reintroduce the fiction it exists to remove.
        """
        with pytest.raises(TypeError):
            audit_write(
                session, entity_type="property", entity_id=uuid.uuid4(), action="create"
            )

    def test_successful_action_audits_through_the_callers_session(self, session, account_a):
        """A successful action's audit row commits *with* the change it describes.

        An audit row for a change that never landed is worse than no audit row, so this path
        deliberately does **not** use an independent transaction.
        """
        row = audit_write(
            session,
            entity_type="property",
            entity_id=uuid.uuid4(),
            action="create",
            actor="user-123",
            changes={"name": "Belle Estate"},
        )
        assert row.actor == "user-123"
        assert row.account_id == account_a
