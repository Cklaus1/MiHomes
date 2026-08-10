"""Waitlist business logic — signup, double opt-in confirmation, queue position.

Phase 0 only, and GLOBAL: no `account_id`, because this ships before `accounts`
exists (SPEC-001 D4).

Two rules from §7 shape the whole module:

- **N3 — never reveal whether an email is already on the list.** `signup` is an
  upsert that returns the same shape for a new and an existing address, and it
  resends the confirmation to an unconfirmed row rather than reporting a
  conflict. A distinguishable response would make the endpoint an
  email-enumeration oracle.
- **D7 — double opt-in.** A row is not "confirmed" until the emailed link is
  clicked, and the Phase 0 gate counts *confirmed* signups (GTM:293).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mihomes.models.waitlist import Waitlist

__all__ = [
    "CONFIRM_TOKEN_TTL",
    "MAX_CONFIRM_SENDS",
    "confirm",
    "confirmed_count",
    "normalize_email",
    "position",
    "signup",
]

# Neither value is fixed by SPEC-001 — it requires expiry (A7) and says
# confirm_send_count "bounds resend abuse" (§5.4) without naming numbers. These
# are our defaults, kept as module constants so a founder decision can change
# them in one place.
CONFIRM_TOKEN_TTL = timedelta(days=7)
MAX_CONFIRM_SENDS = 5

# Deliberately permissive: this rejects the obviously-broken (no @, spaces, an
# empty local or domain part) and nothing more. An aggressive regex rejects valid
# addresses, and the confirmation email is the real proof that an address works.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_UTM_FIELDS = ("utm_campaign", "utm_source", "utm_medium")


def normalize_email(raw: str) -> str:
    """Lowercase, strip. Raises ValueError if not a plausible address.

    Deliberately NOT aggressive: no plus-address stripping, no dot-folding.
    Gmail treats a+b@gmail.com as a@gmail.com but most providers do not, and
    silently merging two people's signups is worse than a duplicate row.
    """
    if raw is None:
        raise ValueError("email is required")
    cleaned = raw.strip().lower()
    if not _EMAIL_RE.match(cleaned):
        raise ValueError(f"not a plausible email address: {raw!r}")
    return cleaned


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; Postgres returns tz-aware ones.

    Comparing the two raises TypeError, so normalize before any arithmetic —
    this is M7's hazard in opportunities.md, met here at the read boundary.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def signup(
    session: Session,
    *,
    email: str,
    name: str | None = None,
    num_homes: str | None = None,
    has_staff: bool | None = None,
    source: str = "form",
    utm: dict[str, str] | None = None,
    referred_by: str | None = None,
    signup_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Waitlist, str | None]:
    """Create or update a waitlist row. Idempotent per email (GTM:206 'upsert on repeat').

    Returns (row, raw_confirm_token). The raw token is returned exactly once, for the
    email; only its hash is persisted. Returns (row, None) when the row is already
    confirmed — an already-confirmed signup does not get a new token.
    """
    address = normalize_email(email)
    row = session.execute(
        select(Waitlist).where(Waitlist.email == address)
    ).scalar_one_or_none()

    if row is None:
        row = Waitlist(email=address, source=source)
        session.add(row)

    # Refresh the optional details on every signup: a repeat submission is the
    # user correcting or completing their answers, not a duplicate to discard.
    for field, value in (
        ("name", name),
        ("num_homes", num_homes),
        ("has_staff", has_staff),
        ("referred_by", referred_by),
        ("signup_ip", signup_ip),
        ("user_agent", user_agent),
    ):
        if value is not None:
            setattr(row, field, value)

    if utm:
        for field in _UTM_FIELDS:
            if utm.get(field) is not None:
                setattr(row, field, utm[field])

    # Already confirmed: nothing to send, and re-tokenizing would invalidate
    # nothing useful while letting a stranger reset someone else's token.
    if row.confirmed_at is not None:
        session.flush()
        return row, None

    sends = row.confirm_send_count or 0
    if sends >= MAX_CONFIRM_SENDS:
        # Silent per N3 — the caller renders the same success state regardless,
        # so the ceiling must not raise or the response would become an oracle.
        session.flush()
        return row, None

    raw_token = secrets.token_urlsafe(32)
    row.confirm_token_hash = _hash_token(raw_token)
    row.confirm_sent_at = datetime.now(timezone.utc)
    row.confirm_send_count = sends + 1
    session.flush()
    return row, raw_token


def confirm(session: Session, *, raw_token: str) -> Waitlist | None:
    """Confirm by raw token. Returns the row, or None if unknown/expired.

    Idempotent: confirming an already-confirmed row returns it unchanged rather
    than erroring — users click links twice, and mail scanners pre-fetch them.
    """
    if not raw_token:
        return None

    row = session.execute(
        select(Waitlist).where(Waitlist.confirm_token_hash == _hash_token(raw_token))
    ).scalar_one_or_none()
    if row is None:
        return None

    if row.confirmed_at is not None:
        return row  # idempotent: do not move the timestamp

    sent_at = _as_utc(row.confirm_sent_at)
    if sent_at is not None and datetime.now(timezone.utc) - sent_at > CONFIRM_TOKEN_TTL:
        return None

    row.confirmed_at = datetime.now(timezone.utc)
    session.flush()
    return row


def position(session: Session, row: Waitlist) -> int:
    """1-based queue position by created_at among confirmed rows (GTM:212).

    O4 default is "compute it, do not display it" — the value is available for the
    confirmation email if the founder decides to show it.
    """
    if row.confirmed_at is None:
        raise ValueError("position is only defined for a confirmed row")

    # Rank in Python on (created_at, id) rather than in SQL.
    #
    # `created_at` comes from a server default, so rows created in the same second
    # carry an IDENTICAL timestamp — a bare `created_at < row.created_at` counted a
    # peer, and even the row itself, as "ahead" (observed: position 2 for the only
    # confirmed row). UUIDv7 ids sort in creation order by construction (why D5
    # chose v7), so they are the natural tie-break.
    #
    # Doing that comparison in SQL is not portable here: SQLite persists the
    # PGUUID column as an UNDASHED hex string ('019fed54a792…') while the ORM binds
    # a dashed uuid.UUID, so `Waitlist.id < row.id` never orders correctly and a
    # row-value `tuple_(...)` comparison silently matches nothing. Phase 0 is
    # Postgres-only (D3), but the unit suite runs on SQLite, and a ranking that is
    # right on one engine and silently wrong on the other is worse than one that is
    # right on both.
    #
    # The waitlist is a Phase-0 signup list, so the confirmed set is small enough
    # to rank in memory; if that ever stops being true this becomes a window
    # function over (created_at, id).
    confirmed = session.execute(
        select(Waitlist.id, Waitlist.created_at).where(Waitlist.confirmed_at.is_not(None))
    ).all()
    ranked = sorted(confirmed, key=lambda r: (r.created_at, str(r.id)))
    for index, candidate in enumerate(ranked, start=1):
        if candidate.id == row.id:
            return index
    raise ValueError("row is not in the confirmed set")


def confirmed_count(session: Session) -> int:
    """Confirmed signups — the Phase 0 gate metric (GTM:293).

    O3 sets the threshold; this only reports the number.
    """
    return int(
        session.execute(
            select(func.count())
            .select_from(Waitlist)
            .where(Waitlist.confirmed_at.is_not(None))
        ).scalar_one()
    )
