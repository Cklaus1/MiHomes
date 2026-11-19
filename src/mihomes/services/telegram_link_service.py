"""Telegram sender → membership, and the scope that follows — SPEC-003 §6 Step 16.

F5's finding: *"sender identity is captured and then discarded."* `client.py` builds `sender` on
every message, it is read at exactly **one** place in the whole codebase (the PTO approver check),
`senderUsername` is never read, and **nothing is persisted**. Scope is chat-level and
sender-independent, so *"any member of a linked group can ask anything and receives the full
property-scoped estate context — open issues, assets, staff roster, budgets."*

**D16 — an unlinked sender is treated as STAFF-LEVEL, not denied**, and that is a deliberate
departure from `TELEGRAM_PRD:158`'s deny-by-default. Deny-by-default on day one would silence the
bot for the entire Belle group — *including the founder*, since no links exist yet. Staff-level is
restrictive enough to close the leak and makes linking an upgrade rather than a prerequisite.

**This module is also where G10's deviation stops being a risk.** The scope travels in a
ContextVar that defaults to *unrestricted*, so a bot path that binds nothing fails **open**.
`sender_authz` binds explicitly and always — an unresolved sender gets `staff` with an empty
scope, which is the most restrictive combination available, never the default.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.membership import Membership
from mihomes.models.telegram_link import TelegramLink

__all__ = [
    "LINK_CODE_TTL",
    "UNLINKED_ROLE",
    "hash_code",
    "link_sender",
    "resolve_sender",
    "sender_authz",
]

#: `TELEGRAM_PRD:126-127` — short-lived, single-use, hashed codes.
LINK_CODE_TTL = timedelta(minutes=15)

#: D16. Named rather than inlined so the deliberate departure from deny-by-default is greppable.
UNLINKED_ROLE = "staff"

_MEMBERSHIPS = Membership.__table__


def hash_code(raw: str) -> str:
    """`sha256(raw)` — link codes are stored hashed, like sessions and invite tokens."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_sender(
    session: Session, telegram_user_id: int, account_id: uuid.UUID
) -> Membership | None:
    """The membership this sender is linked to, or `None`.

    `None` → treat as **staff-level** (D16), never as denied and never as unrestricted.

    **A revoked membership fails resolution**, and it does so structurally: the link row
    `CASCADE`s when the membership row is deleted, and the join below requires
    `status = 'active'`, so revoking access closes the bot the same way it closes the web app
    (A32). Two mechanisms, because the first only covers deletion and revocation is a status
    change.

    Read with a Core select for the same reason `auth/sessions.py` does: this runs *before* any
    tenant context exists — resolving the sender is how the account is discovered — so an ORM read
    of `Membership` would demand the context it is trying to establish.
    """
    row = session.execute(
        select(_MEMBERSHIPS.c.id)
        .select_from(
            TelegramLink.__table__.join(
                _MEMBERSHIPS, TelegramLink.__table__.c.membership_id == _MEMBERSHIPS.c.id
            )
        )
        .where(
            TelegramLink.__table__.c.telegram_user_id == telegram_user_id,
            TelegramLink.__table__.c.account_id == account_id,
            _MEMBERSHIPS.c.status == "active",
        )
    ).first()

    return session.get(Membership, row.id) if row else None


@contextmanager
def sender_authz(session: Session, telegram_user_id: int | None, account_id: uuid.UUID):
    """Bind role and property scope for one bot message. **Always binds.**

    This is the answer to G10's recorded deviation. `current_property_scope` defaults to `None`
    meaning *unrestricted*, so any path that forgets to bind fails open — and the bot is the one
    consumer that runs entirely outside a web request. Binding unconditionally here means the
    question is never "did someone remember?".

    Three cases, and the two that are not the happy path both fail closed:

    - **linked** → that membership's role and scope, exactly as the web app would compute them;
    - **unlinked** → `staff` with an **empty** scope. D16 says staff-*level*; the empty scope is
      this module's addition, because a staff role with no scope rows is what D3 already defines
      as "zero properties, never all";
    - **no sender at all** (a channel post, an edited message with no `from`) → the same.
    """
    from mihomes.authz.scope import authz_context, scoped_property_ids

    membership = (
        resolve_sender(session, telegram_user_id, account_id)
        if telegram_user_id is not None
        else None
    )

    if membership is None:
        with authz_context(UNLINKED_ROLE, frozenset()):
            yield None
        return

    scope = scoped_property_ids(session, membership)
    with authz_context(membership.role, scope):
        yield membership


def link_sender(
    session: Session,
    account_id: uuid.UUID,
    membership_id: uuid.UUID,
    telegram_user_id: int,
) -> TelegramLink:
    """Bind a Telegram sender to a membership.

    **R2 (row 20) — linking is self-only for every role**, enforced by the caller that knows who
    is asking; this function performs the write. The link *grants no additional data access*:
    every resolved message re-enters the same role and scope computation the web app uses, which
    is what `sender_authz` above does.

    Re-linking the same sender updates the existing row rather than raising: the unique constraint
    is `(account_id, telegram_user_id)`, and someone re-running `/link` after changing devices is
    doing the expected thing.
    """
    existing = session.execute(
        select(TelegramLink).where(
            TelegramLink.account_id == account_id,
            TelegramLink.telegram_user_id == telegram_user_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.membership_id = membership_id
        existing.linked_at = datetime.now(timezone.utc)
        session.flush()
        return existing

    link = TelegramLink(
        account_id=account_id,
        membership_id=membership_id,
        telegram_user_id=telegram_user_id,
    )
    session.add(link)
    session.flush()
    return link


def new_link_code() -> tuple[str, str]:
    """`(raw_code, hash)` — short, single-use, hashed at rest (`TELEGRAM_PRD:126-127`).

    Short because a human types it into a chat window; short-lived (15 minutes) *because* it is
    short. The two decisions are the same decision.
    """
    raw = secrets.token_hex(4).upper()
    return raw, hash_code(raw)
