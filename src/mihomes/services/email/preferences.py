"""Who gets which email — the Emails page and every sender read this (2026-11-23).

Two layers, and a member receives an optional email only when both say yes:

1. **The household's team settings** (owners and admins): which roles get each optional email.
   Stored in `configurations` as `email.roles.<type>`, a comma list, defaulting per type below.
2. **The member's own switch**: anyone may turn off an email they were given. They cannot turn
   on one their role was not given; the switch is simply not offered.

Required emails (sign-in security, and billing for the owner) are listed so the page can show
them as always on, but nothing here can stop them: they are `transactional` in `EmailService`
and do not consult this module.

The chat digest's personal switch lives in `chat_notify_prefs.email_digest` (it predates this
module); everything else in `email_preferences`. Only this module knows that.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

ROLES = ("owner", "admin", "staff")
ROLE_LABELS = {"owner": "Owner", "admin": "Admins", "staff": "Staff"}


@dataclass(frozen=True)
class EmailType:
    key: str
    label: str
    description: str
    required: bool
    #: Roles this email may ever go to. Anything with money in it is owner/admin only: staff
    #: cannot see costs in the app, so an email must not show them either.
    allowed_roles: tuple[str, ...]
    #: Roles that get it before anyone changes the team settings.
    default_roles: tuple[str, ...]
    #: Extra condition shown on the page, e.g. which plan includes it.
    note: str = ""


EMAIL_TYPES: tuple[EmailType, ...] = (
    EmailType(
        "security", "Account security",
        "Confirming your email, password resets, and notices when a sign-in method changes.",
        required=True, allowed_roles=ROLES, default_roles=ROLES,
    ),
    EmailType(
        "billing", "Billing",
        "Failed payments, your trial ending, and cancellation confirmations.",
        required=True, allowed_roles=("owner",), default_roles=("owner",),
    ),
    EmailType(
        "weekly_report", "Weekly estate report",
        "A summary every Monday of what happened across your homes and what's coming up.",
        required=False, allowed_roles=("owner", "admin"), default_roles=("owner", "admin"),
        note="Included with the Estate plan.",
    ),
    EmailType(
        "chat_digest", "Unread chat messages",
        "A short email when chat messages have waited about an hour while you were away.",
        required=False, allowed_roles=ROLES, default_roles=ROLES,
    ),
    EmailType(
        "onboarding_tips", "Getting-started tips",
        "A few emails in your first weeks on how to get the most from MiHomes.",
        required=False, allowed_roles=("owner",), default_roles=("owner",),
    ),
)
TYPES = {t.key: t for t in EMAIL_TYPES}
OPTIONAL = tuple(t for t in EMAIL_TYPES if not t.required)


def _config_key(email_type: str) -> str:
    return f"email.roles.{email_type}"


def team_roles(session: Session, email_type: str) -> tuple[str, ...]:
    """Roles the household sends this email to. Required types are fixed."""
    t = TYPES[email_type]
    if t.required:
        return t.default_roles
    from mihomes.services.config_service import get_config

    stored = get_config(session, _config_key(email_type))
    if stored is None:
        return t.default_roles
    chosen = {r for r in stored.split(",") if r}
    # Re-filtered on read too, so a stored value can never widen past what the type allows.
    return tuple(r for r in ROLES if r in chosen and r in t.allowed_roles)


def set_team_roles(session: Session, email_type: str, roles: list[str]) -> None:
    """Save which roles get an optional email. Roles the type does not allow are dropped, so
    "staff" can never be given the weekly report however the form is posted. Does not commit."""
    t = TYPES[email_type]
    if t.required:
        raise ValueError(f"{email_type} is required and has no team setting")
    from mihomes.services.config_service import set_config

    kept = [r for r in ROLES if r in roles and r in t.allowed_roles]
    set_config(session, _config_key(email_type), ",".join(kept))


def personal_switch(session: Session, membership, email_type: str) -> bool:
    """The member's own switch: True unless they turned it off."""
    if email_type == "chat_digest":
        from mihomes.models.chat_notify import ChatNotifyPref

        prefs = session.execute(
            select(ChatNotifyPref).where(ChatNotifyPref.membership_id == membership.id)
        ).scalar_one_or_none()
        return True if prefs is None else bool(prefs.email_digest)

    from mihomes.models.email_preference import EmailPreference

    row = session.execute(
        select(EmailPreference.enabled).where(
            EmailPreference.membership_id == membership.id,
            EmailPreference.email_type == email_type,
        )
    ).scalar_one_or_none()
    return True if row is None else bool(row)


def set_personal_switch(session: Session, membership, email_type: str, enabled: bool) -> None:
    """Flip one member's switch for one optional email. Does not commit."""
    if TYPES[email_type].required:
        raise ValueError(f"{email_type} is required and cannot be turned off")
    from mihomes.ids import new_id

    if email_type == "chat_digest":
        from mihomes.models.chat_notify import ChatNotifyPref

        stmt = pg_insert(ChatNotifyPref.__table__).values(
            id=new_id(), account_id=membership.account_id, membership_id=membership.id,
            email_digest=enabled,
        ).on_conflict_do_update(
            index_elements=["account_id", "membership_id"], set_={"email_digest": enabled},
        )
        session.execute(stmt)
        return

    from mihomes.models.email_preference import EmailPreference

    stmt = pg_insert(EmailPreference.__table__).values(
        id=new_id(), account_id=membership.account_id, membership_id=membership.id,
        email_type=email_type, enabled=enabled,
    ).on_conflict_do_update(
        index_elements=["account_id", "membership_id", "email_type"], set_={"enabled": enabled},
    )
    session.execute(stmt)


def wants(session: Session, membership, email_type: str) -> bool:
    """Should this member receive this email? Team settings AND their own switch.

    Active members only: a revoked or pending member gets nothing optional.
    """
    if getattr(membership, "status", "active") != "active":
        return False
    if membership.role not in team_roles(session, email_type):
        return False
    return TYPES[email_type].required or personal_switch(session, membership, email_type)


def recipients(session: Session, account_id, email_type: str) -> list[tuple[str, str | None]]:
    """`(email, name)` for every active member of this account who wants this email."""
    from mihomes.models.membership import Membership
    from mihomes.models.user import User

    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.account_id == account_id, Membership.status == "active")
        .order_by(Membership.role, User.email)
    ).all()
    return [(u.email, u.name) for m, u in rows if u.email and wants(session, m, email_type)]


def page_rows(session: Session, membership) -> list[dict]:
    """What the "Emails you get" list shows for this member, in registry order. An optional
    email their role is not given is left out, so nobody sees a switch that does nothing."""
    rows = []
    for t in EMAIL_TYPES:
        if membership.role not in team_roles(session, t.key):
            continue
        rows.append({
            "type": t,
            "on": True if t.required else personal_switch(session, membership, t.key),
        })
    return rows


def team_rows(session: Session) -> list[dict]:
    """What "What your team gets" shows: each optional email and which roles get it."""
    return [
        {"type": t, "roles": set(team_roles(session, t.key))} for t in OPTIONAL
    ]
