"""Invites — create / resend / revoke / accept (SPEC-003 §6 Step 12; A19, A20, A21).

**The token is the authority, not the email** (D5, `ONBOARDING` §6.3). Whoever presents a valid
token is the invitee; the address is a delivery detail. That is what makes forwarding work, and
it is why §6.3 pairs it with *mismatch notification* rather than with a hard email check — a
check would break the legitimate case (a person signing in with a different Google address) while
stopping nobody who already has the link.

**Only the hash is stored** (D5), the same discipline as `sessions` and SPEC-001's confirm token:
a database disclosure must not yield usable invitations.

**A pending invite consumes a seat from the moment it is created** (D6, `PRICING` §3.1 as
corrected by `PRD_REVIEW` A1) — *"so we never email an invite that can't be honored."* Seats are
therefore counted across **two tables**, active memberships plus pending invites, and revoking or
expiring an invite frees its seat immediately.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mihomes.ids import new_id
from mihomes.models.account import Account
from mihomes.models.invite import Invite
from mihomes.models.membership import Membership, MembershipPropertyScope

__all__ = [
    "INVITE_TTL",
    "InviteError",
    "SeatLimitReached",
    "accept_invite",
    "create_invite",
    "find_pending",
    "hash_token",
    "revoke_invite",
    "seats_used",
]

#: The Core table, for the two lookups that run **before** an account is known — `find_pending`
#: and `accept_invite`. An ORM `select(Invite)` passes through the tenancy listener, which reads
#: `current_account` and raises when nothing is bound; an invitee has nothing bound, because the
#: invitation is what is about to give them an account. Same carve-out `auth/sessions.py:65`
#: takes for the membership read behind sign-in.
_INVITES = Invite.__table__

#: B9 — the `PLACEHOLDER` in `ONBOARDING:167`, resolved to the locked 7-day value.
INVITE_TTL = timedelta(days=7)

_TOKEN_BYTES = 32


class InviteError(Exception):
    """An invite could not be created or accepted. Carries a caller-safe message."""


class SeatLimitReached(InviteError):
    """The account is at its seat cap (`PRICING` §3.2 rule 5)."""


def hash_token(raw: str) -> str:
    """`sha256(raw)` as hex — what the database stores.

    A plain digest, not a KDF: the token is 256 bits of `secrets` output, so there is no
    dictionary to attack and a slow hash would only add latency. Salting would break the lookup,
    since the point is to find a row *by* the hash. Same reasoning as `auth/sessions.py`.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def seats_used(session: Session, account_id: uuid.UUID) -> int:
    """Active memberships **plus** pending invites — D6, and it is two tables on purpose.

    `memberships.status` is only `active | revoked`; there is no `invited` state (N7), because an
    invitee has no `user_id` yet. So a seat count that read one table would undercount by exactly
    the number of outstanding invitations — and the account would email invites it could not
    honour, which is the failure `PRICING` §3.1 calls out by name.
    """
    memberships = session.execute(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.account_id == account_id,
            Membership.status == "active",
        )
    ).scalar_one()

    pending = session.execute(
        select(func.count())
        .select_from(Invite)
        .where(
            Invite.account_id == account_id,
            Invite.status == "pending",
            Invite.expires_at > datetime.now(timezone.utc),
        )
    ).scalar_one()

    return memberships + pending


def _seat_limit(session: Session, account_id: uuid.UUID) -> int:
    from mihomes.entitlements import limits_for

    account = session.get(Account, account_id)
    limits = limits_for(
        getattr(account, "plan", "free"), getattr(account, "subscription_status", None)
    )
    return limits["max_seats"]


def create_invite(
    session: Session,
    account_id: uuid.UUID,
    inviter_id: uuid.UUID | None,
    email: str,
    role: str,
    property_ids: list[uuid.UUID] | None = None,
) -> tuple[Invite, str]:
    """Create an invite. Returns `(invite, plaintext_token)` — **only the hash is persisted**.

    The raw token is returned rather than stored because this is the only moment it exists in the
    process; the caller emails it and it is unrecoverable afterwards, by design.

    **A staff invite with zero properties is rejected** (A21, D3, `ONBOARDING:164`). Creating it
    would produce a member who can sign in and see nothing — indistinguishable, to them, from the
    product being broken — and the fail-closed direction of D3 means the fix cannot be "grant all".
    """
    role = role.strip().lower()
    if role not in ("admin", "staff"):
        # D2: `owner` can never be *assigned*, only transferred. An invite is an assignment.
        raise InviteError(f"cannot invite with role {role!r}; ownership moves only by transfer")

    property_ids = list(property_ids or [])
    if role == "staff" and not property_ids:
        raise InviteError(
            "a staff invite must name at least one property — staff scope is a whitelist, "
            "and zero scope rows means zero properties visible (D3)"
        )

    _check_seat_capacity(session, account_id)

    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    invite = Invite(
        id=new_id(),
        account_id=account_id,
        email=email.strip().lower(),
        role=role,
        property_ids=[str(p) for p in property_ids],
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) + INVITE_TTL,
        status="pending",
        created_by=inviter_id,
    )
    session.add(invite)
    session.flush()
    return invite, raw


def _check_seat_capacity(session: Session, account_id: uuid.UUID) -> None:
    if seats_used(session, account_id) >= _seat_limit(session, account_id):
        raise SeatLimitReached(
            "this account has no seats left; revoke a pending invite or upgrade"
        )


def find_pending(session: Session, token: str) -> Invite | None:
    """The pending, unexpired invite this token names — or `None`.

    Read-only, for the screen that shows what an invitation grants before it is redeemed.
    Returns `None` rather than raising for every failure, so the page renders one "this
    invitation is no longer valid" state instead of distinguishing unknown from used from
    expired — the same reasoning as `accept_invite`, on a surface reachable before sign-in.

    **The lookup is unscoped, and it has to be — this is the one query that DISCOVERS the
    account.** `Invite` is `TenantOwned`, so an ORM `select(Invite)` goes through the tenancy
    listener, which reads `current_account` and raises `LookupError` when nothing is bound. An
    invitee has no account by definition: the invitation is what is about to give them one.

    Found by SPEC-010 A14, empirically: the route 500'd for a freshly signed-up invitee. The
    docstring already said "reachable before sign-in" — the intent was right and the query was
    not. Same carve-out and same mechanism as `auth/sessions.py:65`'s membership read, and the
    same one `resolve_sender` documents: a token lookup that must run before its own tenant
    context exists.

    Isolation is unaffected. The token is 256 bits of `secrets` output and its hash is unique,
    so this selects at most the row whose secret the caller already holds — it cannot enumerate,
    and every later read of that account goes through the scoped path.
    """
    row = session.execute(
        select(_INVITES).where(_INVITES.c.token_hash == hash_token(token))
    ).one_or_none()
    if row is None:
        return None

    # Re-read through the ORM under the account the row itself names, so callers still receive a
    # normal `Invite` and the scoped path is exercised for everything after the discovery.
    from mihomes.tenancy import account_context

    with account_context(row.account_id):
        invite = session.get(Invite, row.id)

    if invite is None or invite.status != "pending":
        return None

    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return None if expires <= datetime.now(timezone.utc) else invite


def revoke_invite(session: Session, invite: Invite) -> Invite:
    """Revoke a pending invite, freeing its seat immediately (D6).

    Idempotent: revoking an already-revoked invite is not an error, because the UI's revoke button
    is exactly the kind of thing that gets double-clicked.
    """
    if invite.status == "pending":
        invite.status = "revoked"
        session.flush()
    return invite


def accept_invite(session: Session, token: str, user) -> Membership:
    """Consume an invite and create the membership — **transactionally** (A19).

    `PRICING` §3.2 rule 5: the seat count is re-checked **inside** the transaction, *"so races
    (two concurrent invites at the seat cap) cannot exceed a limit."* The re-check alone is not
    enough — two transactions can both read "one seat free" before either writes. The account row
    is therefore locked `FOR UPDATE` first, which serialises acceptances per account: the second
    transaction blocks until the first commits, then re-reads a count that includes it.

    Locking the *account* rather than the invite is deliberate. The invariant being protected is
    "seats used ≤ seats allowed", which is a property of the account; two people accepting two
    *different* invites at the cap is exactly the race, and per-invite locks would not see each
    other.
    """
    # Unscoped discovery, then scoped work — see `find_pending` and `_INVITES` for why this
    # cannot be an ORM select. An invitee is authenticated but has no account bound: the
    # invitation is what is about to give them one.
    _row = session.execute(
        select(_INVITES.c.id, _INVITES.c.account_id).where(
            _INVITES.c.token_hash == hash_token(token)
        )
    ).one_or_none()

    if _row is None:
        raise InviteError("this invitation is no longer valid")

    # **Everything below runs with the discovered account bound.** The lookup above is the only
    # unscoped statement; the rest — `refresh`, `seats_used`, the membership and scope inserts —
    # are ordinary tenant work and must go through the normal filter, or they would be a second
    # carve-out rather than one.
    from mihomes.tenancy import account_context

    with account_context(_row.account_id):
        return _accept_locked(session, _row.id, user)


def _accept_locked(session: Session, invite_id, user) -> Membership:
    """The body of `accept_invite`, with the account already bound. See its docstring."""
    invite = session.get(Invite, invite_id)

    # One outcome for unknown, revoked, accepted, and expired — a caller must not be able to
    # distinguish "no such invite" from "already used" by the error text (D9's reasoning, applied
    # to a pre-authentication surface where it matters more).
    if invite is None or invite.status != "pending":
        raise InviteError("this invitation is no longer valid")

    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        invite.status = "expired"
        session.flush()
        raise InviteError("this invitation is no longer valid")

    # Serialise per account. Everything after this point is under the lock.
    session.execute(
        select(Account.id).where(Account.id == invite.account_id).with_for_update()
    ).scalar_one()

    # Re-read the invite's own status under the lock: the first of two concurrent acceptances of
    # the SAME token commits `accepted` while the second was blocked, and the second's copy is
    # stale.
    session.refresh(invite)
    if invite.status != "pending":
        raise InviteError("this invitation is no longer valid")

    # This invite already holds a seat (D6), so it is counted in `seats_used`. Accepting converts
    # it from a pending invite into an active membership — net zero. The check guards the case
    # where the *cap itself* moved, or where memberships were added by another path.
    if seats_used(session, invite.account_id) > _seat_limit(session, invite.account_id):
        raise SeatLimitReached(
            "this account has no seats left; ask an admin to free one or upgrade"
        )

    membership = Membership(
        id=new_id(),
        account_id=invite.account_id,
        user_id=user.id,
        role=invite.role,
        status="active",
        invited_by=invite.created_by,
    )
    session.add(membership)
    session.flush()

    for property_id in invite.property_ids or []:
        session.add(
            MembershipPropertyScope(
                id=new_id(),
                account_id=invite.account_id,
                membership_id=membership.id,
                property_id=uuid.UUID(str(property_id)),
            )
        )

    invite.status = "accepted"
    session.flush()
    return membership


def email_mismatch(invite: Invite, user) -> bool:
    """§6.3 — did this token arrive from an address other than the one invited?

    **Not a rejection.** D5 makes the token the authority, so a mismatch is *notified*, not
    blocked: forwarding an invite to one's own address is a legitimate and common thing to do,
    and a hard check would break it while stopping nobody who already holds the link. The
    notification is what turns a stolen invite into something the account can see.
    """
    return (getattr(user, "email", "") or "").strip().lower() != invite.email
