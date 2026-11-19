"""`audit_write` — SPEC-003 §6 Step 2, A33.

F6: the audit table is **not** greenfield. `models/audit_log.py` already exists and is already
`TenantOwned`; what is missing is a *real actor*. `AuditLog.actor` defaults to `"admin"` and the
bot never overrides it, so every current call site records a fictional actor. This module is the
thread that carries the real one.

**Denies and successes are written in different transactions, and that is the whole design.**

- A **successful** privileged action is audited through the *caller's* session, so the audit row
  and the change it describes commit or roll back together. An audit row for a change that never
  landed is worse than none.
- A **deny** is audited through an *independent* session that commits immediately, because the
  request transaction is about to be rolled back. FastAPI's `get_db` does
  `except Exception: s.rollback(); raise`, and `HTTPException` is an `Exception` — so a deny
  audit written through the request session is **discarded by the very mechanism that reports
  the denial**. A33 says *every deny* writes a row; through the request session that sentence
  would be false in production while passing any test that asserts within the transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from mihomes.models.audit_log import AuditLog

__all__ = ["audit_deny", "audit_write"]

# A deny that names no row still needs an `entity_id` — the column is NOT NULL. Using the
# account id would collide with genuine account-level audit rows, so denials that have no target
# are stamped with this sentinel, which is not a valid row id anywhere.
NO_TARGET = uuid.UUID("00000000-0000-0000-0000-000000000000")


def audit_write(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    actor: str,
    changes: dict[str, Any] | None = None,
) -> AuditLog:
    """Record an audited event through the caller's session.

    Use for **successful** actions: the row commits with the change it describes, so the two
    cannot disagree. `actor` is required and has no default — the whole point of F6 is that the
    existing `"admin"` default is a fiction, and a parameter with a default invites it back.
    """
    row = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        changes=changes,
    )
    session.add(row)
    session.flush()
    return row


def audit_deny(
    *,
    account_id: uuid.UUID,
    actor: str,
    action: str,
    role: str,
    reason: str,
    target_id: uuid.UUID | None = None,
    session_factory=None,
) -> None:
    """Record a refusal in its **own** transaction, so it survives the request's rollback.

    `session_factory` exists for tests, which run inside a transaction that is deliberately never
    committed; passing one lets a test observe the row without this function reaching the real
    database. In production it is `None` and an independent session is opened.

    **Never raises.** An audit failure must not convert a clean 403 into a 500 — the caller is
    already on its way to refusing the request, and losing the record is strictly better than
    losing the refusal. The failure is swallowed here and only here, deliberately, against the
    codebase's usual ban on silent excepts (hardening R1).
    """
    from mihomes.tenancy import account_context

    def _write(session: Session) -> None:
        audit_write(
            session,
            entity_type="authz",
            entity_id=target_id or NO_TARGET,
            action="deny",
            actor=actor,
            changes={"attempted": action, "role": role, "reason": reason},
        )

    try:
        # The tenant is bound around **both** paths. `AuditLog` is TenantOwned and the G8.3 stamp
        # listener fails closed without a context — and the independent session started below
        # carries none of its own. Binding it here rather than only in the production branch
        # keeps the test path from passing by accident on whatever context happened to be
        # ambient, which is the failure mode C12 was.
        with account_context(account_id):
            if session_factory is not None:
                session = session_factory()
                try:
                    _write(session)
                    session.commit()
                finally:
                    session.close()
                return

            from mihomes.db import get_session

            with get_session() as session:
                _write(session)
                session.commit()
    except Exception:  # pragma: no cover - see the docstring
        import logging

        logging.getLogger(__name__).exception(
            "failed to write deny audit for action=%s actor=%s", action, actor
        )
